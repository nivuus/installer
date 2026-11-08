#!/usr/bin/env python3
"""nivuus-bios: list and change BIOS setup settings from Linux, safely.

The source of truth is the setup menu of the RUNNING firmware, decompiled
from its own image with IFRExtractor-RS (UEFIExtract pulls the Setup module
out of the vendor capsule first, see --help of `import`). Every value shown
is read from the UEFI variable that holds it; every change is validated
against what the menu allows, backed up, written, then read back.

  import FILE         keep FILE as the menu of the running BIOS version
  list [--filter T]   every visible setting with its value and choices
  show KEY            one setting in full (KEY = exact name or 0x id)
  set KEY VALUE       validate, back up, write, verify (asks first)
  restore DIR         put a backup back (asks first)

Changes take effect at the next boot, like any change saved in the menu.
"""

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import efivars  # noqa: E402
import ifr_eval  # noqa: E402
import ifr_model  # noqa: E402
from settings import Refused, Settings  # noqa: E402

STATE = os.environ.get("NIVUUS_BIOS_STATE", "/var/lib/nivuus/bios")
DMI = os.environ.get("NIVUUS_BIOS_DMI", "/sys/class/dmi/id")


def running_bios():
    """(board, version), the key a menu is filed under."""
    def field(name):
        with open(os.path.join(DMI, name)) as fh:
            return fh.read().strip()
    return field("board_name"), field("bios_version")


def ifr_path():
    board, version = running_bios()
    return os.path.join(STATE, "%s-%s" % (board.replace(" ", "_"), version), "setup.ifr.txt")


def load(args):
    path = args.ifr or ifr_path()
    if not os.path.exists(path):
        board, version = running_bios()
        raise Refused("no menu on file for %s BIOS %s (%s). Extract it from that exact "
                      "BIOS image and run: nivuus-bios import <file>" % (board, version, path))
    return Settings(ifr_model.load(path), efivars.EfiVars(args.efivars))


def confirm(args, question):
    if args.yes:
        return True
    if not sys.stdin.isatty():
        raise Refused("not confirmed: run in a terminal, or pass --yes")
    return input("%s [y/N] " % question).strip().lower() in ("y", "yes", "o", "oui")


def state_label(states):
    return ", ".join("%s%s" % (s, "?" if v is ifr_eval.UNKNOWN else "")
                     for s, v in sorted(states.items()))


def cmd_import(args):
    target = ifr_path()
    if os.path.exists(target) and not args.replace:
        raise Refused("%s already exists (use --replace)" % target)
    ifr_model.load(args.file)  # refuse a file this tool cannot read before filing it
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copyfile(args.file, target)
    print("filed as %s" % target)


def cmd_list(args):
    s = load(args)
    rows = []
    for q in s.menu.questions:
        path = " > ".join(q.path)
        if args.filter and args.filter.lower() not in (q.prompt + " " + path).lower():
            continue
        states = s.states(q.conditions)
        if states.get("suppressed") is True and not args.all:
            continue
        rows.append({"id": "%#06x" % q.question_id, "menu": path, "name": q.prompt,
                     "type": q.kind, "value": s.display(q), "choices": s.choices(q),
                     "state": state_label(states)})
    if args.json:
        json.dump(rows, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return
    for r in rows:
        print("%s  %-55s = %-18s  [%s]%s" % (r["id"], (r["menu"] + " > " + r["name"])[-55:],
                                             r["value"], " | ".join(r["choices"])[:70],
                                             "  (" + r["state"] + ")" if r["state"] else ""))
    print("%d settings" % len(rows), file=sys.stderr)


def cmd_show(args):
    s = load(args)
    q = s.find(args.key)
    vs = s.menu.varstores.get(q.varstore_id)
    print("%s\n  menu      %s\n  id        %#06x\n  type      %s\n  value     %s\n  choices   %s"
          % (q.prompt, " > ".join(q.path), q.question_id, q.kind, s.display(q),
             " | ".join(s.choices(q))))
    print("  stored in %s @ %#x (%d bytes)" % (vs.efivar if vs else "?", q.offset, q.size))
    print("  state     %s" % (state_label(s.states(q.conditions)) or "visible, editable"))
    if q.help:
        print("  help      %s" % q.help)


def cmd_set(args):
    s = load(args)
    q = s.find(args.key)
    name, attributes, new, old, shown = s.plan(q, args.value)
    print("%s  (%s)\n  %s  ->  %s" % (q.prompt, " > ".join(q.path), old, shown))
    if old == shown:
        print("already set, nothing written")
        return
    if q.flags & ifr_model.QF_CALLBACK:
        print("  note: the setup menu runs firmware code when this setting changes; that code "
              "does not run here. Check the effect after the next boot.")
    if not confirm(args, "Write it?"):
        print("nothing written")
        return
    saved = s.efivars.backup([name], efivars.backup_dir(os.path.join(STATE, "backups")))
    s.efivars.write(name, attributes, new)
    print("written and read back. Backup: %s\nTakes effect at the next boot. Undo: "
          "nivuus-bios restore %s" % (saved, saved))


def cmd_restore(args):
    ev = efivars.EfiVars(args.efivars)
    names = sorted(os.listdir(args.directory))
    print("restore %s from %s" % (", ".join(names), args.directory))
    if not confirm(args, "Write them back?"):
        print("nothing written")
        return
    changed = ev.restore(args.directory)
    print("restored: %s" % (", ".join(changed) or "nothing differed"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="nivuus-bios", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ifr", help="menu file to use instead of the one filed for this BIOS")
    p.add_argument("--efivars", default=efivars.EFIVARS, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("import", help="file the decompiled menu of the running BIOS")
    i.add_argument("file")
    i.add_argument("--replace", action="store_true")
    ls = sub.add_parser("list", help="list settings")
    ls.add_argument("--filter")
    ls.add_argument("--all", action="store_true", help="include settings the menu hides")
    ls.add_argument("--json", action="store_true")
    sh = sub.add_parser("show", help="one setting in full")
    sh.add_argument("key")
    st = sub.add_parser("set", help="change one setting")
    st.add_argument("key")
    st.add_argument("value")
    st.add_argument("--yes", action="store_true")
    rs = sub.add_parser("restore", help="write a backup back")
    rs.add_argument("directory")
    rs.add_argument("--yes", action="store_true")
    args = p.parse_args(argv)
    try:
        {"import": cmd_import, "list": cmd_list, "show": cmd_show, "set": cmd_set,
         "restore": cmd_restore}[args.command](args)
    except (Refused, efivars.EfiVarError, ifr_model.IfrFormatError) as exc:
        print("refused: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
