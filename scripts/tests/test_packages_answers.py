#!/usr/bin/env python3
"""Tests for installer/packages/answers.py - recording an adopted package's answers.

Run: python3 scripts/tests/test_packages_answers.py
"""
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
ROOT = tempfile.mkdtemp(prefix="nivuus-answers-")
for key, rel in (("NIVUUS_PACKAGES_DIR", "opt/nivuus-packages"),
                 ("NIVUUS_STATE_FILE", "etc/nivuus/packages.json"),
                 ("NIVUUS_STAMP_DIR", "var/lib/nivuus/packages")):
    os.environ[key] = os.path.join(ROOT, rel)
sys.path.insert(0, str(REPO / "installer"))

from packages import answers, state  # noqa: E402
from packages.updater import UpdateError  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_refused(label, fn, needle):
    try:
        fn()
    except UpdateError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        return
    failures.append(f"{label}: expected UpdateError, none raised")


pkg = os.path.join(os.environ["NIVUUS_PACKAGES_DIR"], "desk")
os.makedirs(pkg)
with open(os.path.join(pkg, "nivuus-package.yaml"), "w") as fh:
    fh.write("apiVersion: nivuus.dev/v1\nname: desk\nversion: 0.0.0\n"
             "label: desk\ntier: userspace\nwizard:\n  questions: wizard.yaml\n")
with open(os.path.join(pkg, "wizard.yaml"), "w") as fh:
    fh.write("- key: admin_email\n  type: texte\n  label: Email\n  required: true\n"
             "- key: admin_password\n  type: secret\n  label: Password\n  required: true\n"
             "- key: auth_mode\n  type: choix\n  label: Auth\n"
             "  choices: [motdepasse, pomerium]\n  default: motdepasse\n  required: true\n"
             "- key: vb_audio\n  type: bool\n  label: VB\n  default: false\n")
state.save({"desk": {"version": "0.0.0", "state": "installed"}})

GIVEN = ["admin_email=someone@example.org", "auth_mode=pomerium", "vb_audio=yes"]

check_refused("a secret on the command line is refused",
              lambda: answers.record("desk", GIVEN + ["admin_password=x"],
                                     interactive=True),
              "leave it off the command line")
check_refused("a missing secret without a terminal is refused, named",
              lambda: answers.record("desk", GIVEN, interactive=False),
              "admin_password must be entered on a terminal")
check("nothing was recorded by the refusals", "answers" in state.load()["desk"],
      False)
check_refused("a bool accepts only a boolean word",
              lambda: answers.record("desk", ["vb_audio=maybe"], interactive=True),
              "expects true or false")
check_refused("an unknown key names what is asked",
              lambda: answers.record("desk", ["zone=x"], interactive=True),
              "admin_email")
check_refused("a value outside a choice is refused by the wizard rules",
              lambda: answers.record("desk", GIVEN + ["auth_mode=ldap"], interactive=True,
                                     prompt=lambda _: "pw"),
              "expects one of")

entries = iter(["pw1", "pw2"])
check_refused("two different entries are refused",
              lambda: answers.record("desk", GIVEN, interactive=True,
                                     prompt=lambda _: next(entries)),
              "the two entries differ")

recorded = answers.record("desk", GIVEN, interactive=True, prompt=lambda _: "s3cret")
check("answers typed and validated", recorded,
      {"admin_email": "someone@example.org", "admin_password": "s3cret",
       "auth_mode": "pomerium", "vb_audio": True})
check("written to the state", state.load()["desk"]["answers"], recorded)
check("the secret is masked for display",
      answers.masked("desk", recorded)["admin_password"], "<secret>")

again = answers.record("desk", ["vb_audio=false"], interactive=False)
check("a later call keeps the recorded secret and changes only what it names",
      (again["admin_password"], again["vb_audio"]), ("s3cret", False))
check_refused("a package not installed", lambda: answers.record("ghost", []),
              "not installed")


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all answers tests passed")
