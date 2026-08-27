"""Executing a package's hooks, and the jsonl protocol they speak.

`resolve` is a CONTRACT WITH PACKAGE AUTHORS, not a sandbox: nothing here
stops a resolve hook's subprocess from writing to disk, and a hook that does
will succeed and its write will persist - there is no enforcement mechanism,
and none is in scope. What the pipeline ordering in run.py actually depends
on is narrower than "resolve cannot write": the engine never *asks* resolve
to write anything, and never *uses* anything a resolve hook might have
written - so `bootloader` can safely read the platform block resolve returned
before partitioning runs, without the engine caring whether resolve behaved.
The read-only rule is there to catch accident and ordering mistakes (a
package author reaching for install-phase behaviour one phase too early),
not to defend against a malicious package - one of those owns the machine
from the install phase onward anyway, since install already runs as root.
Concretely: plan_packages() runs resolve BEFORE partition(), so at that
moment the target disk does not exist yet and there is nothing on it to
write to. The realistic accident is therefore not a corrupted target - it is
a resolve hook writing to the LIVE INSTALLER filesystem, whose root runs in
RAM (see CLAUDE.md) and is thrown away at the reboot. Such a write silently
does nothing useful and is gone by first boot, which is exactly the class of
mistake this convention exists to make an author avoid.

The protocol is deliberately a subprocess speaking jsonl on stdout rather than
an imported Python API. A package must be able to run on a Debian that has
never seen this engine (the standalone path), so it cannot import from here;
and a stranger's code running in the installer's own process is a worse idea
than a pipe.

Events a hook may emit, one JSON object per line:
    {"event":"progress","pct":int,"msg":str}
    {"event":"platform","kernel-cmdline":[...],"modules":[...],
     "hugepages-mib":int}          - resolve only
    {"event":"refuse","reason":str}                  - resolve only
    {"event":"done"}                                 - advisory only, see below
Anything else on stdout is relayed as a progress line rather than dropped: a
hook that prints is easier to debug than a hook that is silently truncated -
up to MAX_HOOK_OUTPUT_BYTES (below), past which an accidental print loop must
not be allowed to OOM the installer mid-install.

`done` is advisory, not enforced: nothing here checks that a hook emitted it,
and nothing should start to - only the subprocess exit code decides success
or failure. A hook that is correct but terse (no `done` line) must not be
treated as having failed, which is why enforcing it was rejected. Do not
start relying on `done` for anything: a documented-but-unenforced event is
worse than no event at all, and if that guarantee is ever really needed it
has to be enforced here first, not assumed from the docstring.

A `platform` event is trusted enough to end up on the kernel command line, so
its `kernel-cmdline` and `modules` are refused (HookError) unless they are
genuine lists of strings, and `hugepages-mib` is refused unless it is a
non-negative int that is not a bool - the same rule `manifest.py` already
applies to the static declaration, so a hook cannot be laxer than the file it
resolves. None of these are silently coerced: a hook that speaks the protocol
wrongly is a broken hook, and the operator needs to know which package it was
rather than get a suspiciously-empty command line.

STREAMING, NOT CAPTURE_OUTPUT. `subprocess.run(capture_output=True)` reads a
child's entire stdout into one Python string before this module gets to look
at it, so a cap applied afterwards is cosmetic: by the time it runs, the
whole output has already been materialised in the parent's memory. The live
ISO's root runs in RAM (see CLAUDE.md), so this is not an abstract risk here
- an accidental print loop in a package hook can OOM the installer itself,
on a machine with no screen, mid-install. So `_run_hook` drives the child
with `subprocess.Popen` and reads stdout one line at a time, parsing and
retaining only up to MAX_HOOK_OUTPUT_BYTES; past the cap it keeps draining
the pipe (never accumulating) so the child cannot block on a full pipe, and
emits exactly one warning naming the package and phase. stderr is redirected
to a temporary file rather than a second pipe: reading two pipes off one
child from a single thread is the textbook deadlock (one fills while the
other is being read), and a file has no such limit. The timeout is enforced
by a background timer that kills the child - once killed, the pipe's write
end closes and the blocking read loop unblocks with EOF on its own.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass

from .manifest import Manifest, Platform

# A hook is third-party code; it never gets to hang the install forever.
HOOK_TIMEOUT = {"resolve": 120, "install": 1800, "activate": 7200}

# A progress protocol needs a few kilobytes at most. The realistic threat is
# not a malicious hook (install already runs as root - it needs no stdout
# bomb) but an accidental print loop: past this many bytes of stdout, stop
# retaining more of it rather than let that OOM the installer mid-install.
# The hook itself is not killed for it - only its output past the cap is
# discarded, so a chatty-but-otherwise-correct hook still completes normally.
MAX_HOOK_OUTPUT_BYTES = 1 << 20  # 1 MiB


class HookError(RuntimeError):
    """Raised when a hook fails, times out, or speaks an unusable protocol."""


@dataclass(frozen=True)
class Resolution:
    ok: bool
    reason: str
    platform: Platform


def _context(manifest: Manifest, hw: dict, answers: dict) -> str:
    return json.dumps({
        "package": {"name": manifest.name, "version": manifest.version,
                    "root": manifest.root},
        "hw": hw,
        "answers": answers,
    })


def _run_hook(manifest: Manifest, phase: str, hw: dict, answers: dict,
              root: str = "", emit=None) -> list[dict]:
    """Run one hook and return the events it emitted. [] when none is declared.

    Streams stdout instead of buffering it whole - see the module docstring.
    """
    hook = manifest.hook_path(phase)
    if not hook:
        return []
    if not os.path.isfile(hook):
        raise HookError(
            f"package {manifest.name}: hook '{phase}' declared but missing at {hook}")

    cmd = [sys.executable, hook, "--phase", phase]
    if root:
        cmd += ["--root", root]

    context = _context(manifest, hw, answers)

    # stderr to a real file, never a second pipe (see module docstring for
    # why): it is only ever read back as a short tail on failure.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=stderr_file, text=True, cwd=manifest.root)

        # The context is a few KB - well under a pipe buffer (64 KiB on
        # Linux) - so a plain write-then-close is safe; no feeder thread.
        try:
            proc.stdin.write(context)
        except BrokenPipeError:
            pass  # hook exited (or never reads stdin) before we finished
        finally:
            proc.stdin.close()

        timed_out = threading.Event()

        def _enforce_timeout() -> None:
            # poll() first: a race where the child already exited is
            # harmless (kill() on a Popen with a known returncode is a
            # no-op), this just avoids signalling a reused pid needlessly.
            if proc.poll() is None:
                timed_out.set()
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

        timer = threading.Timer(HOOK_TIMEOUT[phase], _enforce_timeout)
        timer.daemon = True
        timer.start()

        events: list[dict] = []
        consumed = 0
        truncated = False
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                if truncated:
                    # Keep iterating (this keeps draining the pipe) without
                    # accumulating - stopping the read here would fill the
                    # pipe and the hook would block on its own stdout forever.
                    continue
                consumed += len(line) + 1
                if consumed > MAX_HOOK_OUTPUT_BYTES:
                    truncated = True
                    if emit:
                        emit.warn(
                            "packages", 0,
                            f"[{manifest.name}] hook '{phase}' exceeded "
                            f"{MAX_HOOK_OUTPUT_BYTES} bytes of stdout; "
                            "the rest was discarded")
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # Not protocol: relay it rather than drop it. A hook that
                    # prints is far easier to debug than one whose output
                    # vanished.
                    if emit:
                        emit.info("packages", 0, f"[{manifest.name}] {line[:120]}")
                    continue
                if not isinstance(event, dict):
                    continue
                events.append(event)
                if emit and event.get("event") == "progress":
                    emit.info("packages", int(event.get("pct") or 0),
                              f"[{manifest.name}] {str(event.get('msg', ''))[:120]}")
        finally:
            proc.stdout.close()
            proc.wait()
            timer.cancel()

        if timed_out.is_set():
            raise HookError(
                f"package {manifest.name}: hook '{phase}' exceeded "
                f"{HOOK_TIMEOUT[phase]}s and was killed")

        if proc.returncode != 0:
            stderr_file.seek(0)
            detail = stderr_file.read().strip().splitlines()
            tail = detail[-1] if detail else "no stderr"
            raise HookError(
                f"package {manifest.name}: hook '{phase}' exited {proc.returncode} "
                f"({tail})")
    return events


def _require_str_list(value, manifest: Manifest, phase: str, field: str) -> list:
    """A 'platform' event's list fields must be genuine lists of strings.

    `tuple(str(v) for v in value)` on a bare string silently disintegrates it
    into one-character fragments - which would then land on the kernel
    command line. Refuse rather than coerce: a hook that speaks the protocol
    wrongly is a broken hook, and the operator needs to know which package.
    """
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise HookError(
            f"package {manifest.name}: hook '{phase}' emitted a 'platform' "
            f"event whose '{field}' is not a list of strings ({value!r})")
    return value


def _require_nonneg_hugepages(value, manifest: Manifest, phase: str) -> int:
    """Mirror manifest.py's own rule for the static 'hugepages-mib' field.

    `bool` is an `int` subclass in Python, so it is checked explicitly - a
    hook emitting `true` must not silently become 1 hugepage.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HookError(
            f"package {manifest.name}: hook '{phase}' emitted a 'platform' "
            f"event whose 'hugepages-mib' is not a non-negative integer "
            f"({value!r})")
    return value


def run_resolve(manifest: Manifest, hw: dict, answers: dict,
                emit=None) -> Resolution:
    """Read-only phase. Returns the merged platform block, or a refusal."""
    events = _run_hook(manifest, "resolve", hw, answers, emit=emit)

    resolved = Platform()
    for event in events:
        kind = event.get("event")
        if kind == "refuse":
            reason = str(event.get("reason") or "").strip() \
                or "le package a refusé cette machine sans en donner la raison"
            return Resolution(ok=False, reason=reason, platform=manifest.platform)
        if kind == "platform":
            cmdline_raw = event.get("kernel-cmdline")
            if cmdline_raw is None:
                cmdline_raw = []
            modules_raw = event.get("modules")
            if modules_raw is None:
                modules_raw = []
            hugepages_raw = event.get("hugepages-mib")
            if hugepages_raw is None:
                hugepages_raw = 0
            _require_str_list(cmdline_raw, manifest, "resolve", "kernel-cmdline")
            _require_str_list(modules_raw, manifest, "resolve", "modules")
            hugepages = _require_nonneg_hugepages(hugepages_raw, manifest, "resolve")
            resolved = Platform(
                kernel_cmdline=tuple(str(v) for v in cmdline_raw),
                modules=tuple(str(v) for v in modules_raw),
                hugepages_mib=hugepages,
            )
    return Resolution(ok=True, reason="",
                      platform=manifest.platform.merge(resolved))


def run_install(manifest: Manifest, hw: dict, answers: dict, root: str,
                emit=None) -> None:
    """Apply the package to the filesystem at `root`. Raises HookError."""
    _run_hook(manifest, "install", hw, answers, root=root, emit=emit)


def run_activate(manifest: Manifest, hw: dict, answers: dict, emit=None) -> None:
    """Post-reboot phase, on the live system with network. Raises HookError."""
    _run_hook(manifest, "activate", hw, answers, emit=emit)
