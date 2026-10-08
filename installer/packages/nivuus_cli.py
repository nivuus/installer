#!/usr/bin/env python3
"""`nivuus`, the command that follows installed packages to their releases.

    nivuus list                 installed packages, version laid, version available
    nivuus check                refresh what is available, lay nothing
    nivuus update [name...]     lay; with no name, every healthy package behind
    nivuus update --self        lay the latest installer release (this command)
    nivuus status [name]        details, including why a lay failed
    nivuus adopt <package-dir>  record a package laid by hand
    nivuus answers <name> [key=value...]
                                record a package's wizard answers; required
                                secrets are asked for on the terminal
    nivuus facts <name> [key=<json>...]
                                show, or record by hand, what `resolve`
                                would have measured (adopted packages)

Any other first word is handed to `nivuus-<word>` from PATH, the way git
does: `nivuus shell doctor` runs `nivuus-shell doctor` without this command
knowing anything about shell.

Installed as a symlink /usr/local/sbin/nivuus -> this file, which lives in
the installer payload. It resolves its own path, so the symlink can import
`packages` and `common` from where they really are - the same reason
activate_cli.py is run in place.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
INSTALLER_ROOT = os.path.dirname(HERE)
if INSTALLER_ROOT not in sys.path:
    sys.path.insert(0, INSTALLER_ROOT)

from packages import answers, fact_record, releases, self_update, state  # noqa: E402
from packages.adopt import adopt  # noqa: E402
from packages.state import StateError  # noqa: E402
from packages.updater import AVAILABLE_NAME, UpdateError, check, update  # noqa: E402

USAGE = __doc__.split("\n\n")[1]


class _StderrEmit:
    def _write(self, level, msg):
        print(f"[{level}] {msg}", file=sys.stderr, flush=True)

    def info(self, step, pct, msg):
        self._write("info", msg)

    def warn(self, step, pct, msg):
        self._write("warn", msg)

    def error(self, step, pct, msg):
        self._write("error", msg)


def _available() -> dict:
    path = os.path.join(state.STAMP_DIR, AVAILABLE_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def cmd_list(_args) -> int:
    current = state.load()
    if not current:
        print(f"no package recorded in {state.STATE_FILE}")
        print(f"installer (this command): {self_update.installed_version()}")
        return 0
    available = _available()
    known = available.get("packages", {})
    rows = [("PACKAGE", "INSTALLED", "AVAILABLE", "STATE")]
    for name, record in sorted(current.items()):
        rows.append((name, record.get("version", "?"),
                     known.get(name, {}).get("available", "-"),
                     state.status(record)))
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
    print(f"\ninstaller (this command): {self_update.installed_version()}")
    checked = available.get("checked_at")
    print(f"\nlast check: {checked}" if checked else "\nnever checked: run 'nivuus check'")
    return 0


def cmd_check(_args) -> int:
    result = check()
    for pending in result.pending:
        print(f"{pending.name}: {pending.installed} -> {pending.available}")
    for name in result.current:
        print(f"{name}: up to date")
    unreachable = list(result.unreachable)
    try:
        installed, release = self_update.check_self()
    except releases.ReleaseError as exc:
        unreachable.append(("installer", str(exc)))
    else:
        behind = (releases.version_key(release.version)
                  > releases.version_key(installed))
        print(f"installer: {installed} -> {release.version} (nivuus update --self)"
              if behind else "installer: up to date")
    for name, reason in result.skipped:
        print(f"{name}: not followed - {reason}", file=sys.stderr)
    for name, reason in unreachable:
        print(f"{name}: could not be checked - {reason}", file=sys.stderr)
    # A check that could not reach a followed package's releases did not do
    # its job: the timer unit must show it failed, not succeed every day.
    return 1 if unreachable else 0


def cmd_update(args) -> int:
    if "--self" in args:
        # Never in the same invocation as packages: an updater half replaced
        # must not go on to lay anything.
        if args != ["--self"]:
            print("usage: nivuus update --self (alone)", file=sys.stderr)
            return 2
        version, changed = self_update.update_self()
        print(f"installer: updated to {version}" if version
              else "installer: already up to date")
        for unit in changed:
            print(f"installer: systemd unit {unit} refreshed")
        return 0
    laid = update(args or None, emit=_StderrEmit())
    print("\n".join(f"{name}: updated" for name in laid) or "nothing to update")
    return 0


def cmd_status(args) -> int:
    current = state.load()
    names = args or sorted(current)
    for name in names:
        if name not in current:
            raise UpdateError(f"{name}: not installed on this machine")
        record = {k: v for k, v in current[name].items() if k != "answers"}
        record["state"] = state.status(current[name])
        print(json.dumps({name: record}, indent=2, ensure_ascii=False))
    return 0


def cmd_adopt(args) -> int:
    if len(args) != 1:
        print("usage: nivuus adopt <package-dir>", file=sys.stderr)
        return 2
    print(f"{adopt(args[0])}: adopted")
    return 0


def cmd_answers(args) -> int:
    if not args:
        print("usage: nivuus answers <name> [key=value...]", file=sys.stderr)
        return 2
    name, assignments = args[0], args[1:]
    recorded = answers.record(name, assignments)
    print(json.dumps({name: answers.masked(name, recorded)}, indent=2,
                     ensure_ascii=False))
    return 0


def cmd_facts(args) -> int:
    if not args:
        print("usage: nivuus facts <name> [key=<json>...]", file=sys.stderr)
        return 2
    name, assignments = args[0], args[1:]
    recorded = fact_record.record(name, assignments)
    print(json.dumps({name: recorded}, indent=2, ensure_ascii=False))
    return 0


COMMANDS = {"list": cmd_list, "check": cmd_check, "update": cmd_update,
            "status": cmd_status, "adopt": cmd_adopt, "answers": cmd_answers,
            "facts": cmd_facts}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if len(argv) >= 2 else 2
    word, args = argv[1], argv[2:]
    if word not in COMMANDS:
        delegate = shutil.which(f"nivuus-{word}")
        if delegate is None:
            print(f"nivuus: unknown command {word!r}\n{USAGE}", file=sys.stderr)
            return 2
        os.execv(delegate, [delegate, *args])
    try:
        return COMMANDS[word](args)
    except (UpdateError, releases.ReleaseError, StateError) as exc:
        print(f"nivuus {word}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
