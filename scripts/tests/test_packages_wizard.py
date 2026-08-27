#!/usr/bin/env python3
"""Tests for installer/packages/wizard.py - the restricted question vocabulary.

A third-party package must not be able to draw arbitrary forms in the portal,
so the vocabulary is closed at six types rather than open at JSON Schema. Two
of them (disque, gpu) exist precisely because they need the ENGINE's hardware
detection to render at all - a package cannot draw its own disk picker.

Run: python3 scripts/tests/test_packages_wizard.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer"))

from packages.wizard import (  # noqa: E402
    QUESTION_TYPES, Question, WizardError, load_questions, validate_answers,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_raises(label, fn, needle):
    try:
        fn()
    except WizardError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        return
    failures.append(f"{label}: expected WizardError, none raised")


QUESTIONS_YAML = """- key: dedicated_disk
  type: disque
  label: "Disque dédié à la console"
  required: true
- key: admin_password
  type: secret
  label: "Mot de passe administrateur"
  required: true
- key: retro
  type: bool
  label: "Retrogaming"
  default: false
- key: edition
  type: choix
  label: "Édition"
  choices: [ltsc, pro]
  default: ltsc
"""

with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "wizard.yaml"
    path.write_text(QUESTIONS_YAML)
    questions = load_questions(str(path))

check("all questions loaded", len(questions), 4)
check("keys preserved in order",
      [q.key for q in questions],
      ["dedicated_disk", "admin_password", "retro", "edition"])
check("choices parsed", questions[3].choices, ("ltsc", "pro"))
check("default parsed", questions[3].default, "ltsc")
check("required defaults to false", questions[2].required, False)
check("every type is a known one",
      {q.type for q in questions} - set(QUESTION_TYPES), set())

# A secret must never carry a default back to the browser.
secret_dict = questions[1].to_dict()
check("secret exposes no default", "default" in secret_dict, False)
check("non-secret exposes its default", questions[2].to_dict()["default"], False)

with tempfile.TemporaryDirectory() as tmp:
    bad = pathlib.Path(tmp) / "w.yaml"
    bad.write_text("- key: x\n  type: freeform\n  label: X\n")
    check_raises("unknown question type refused",
                 lambda: load_questions(str(bad)), "freeform")

    dup = pathlib.Path(tmp) / "dup.yaml"
    dup.write_text("- key: x\n  type: bool\n  label: A\n"
                   "- key: x\n  type: bool\n  label: B\n")
    check_raises("duplicate key refused", lambda: load_questions(str(dup)),
                 "duplicate")

    nochoice = pathlib.Path(tmp) / "nc.yaml"
    nochoice.write_text("- key: x\n  type: choix\n  label: X\n")
    check_raises("choix without choices refused",
                 lambda: load_questions(str(nochoice)), "choices")

    # A default is an answer the operator never gets to correct - if it does
    # not satisfy the question's own constraints, an unanswered question
    # would hand a bad value straight to a hook. Caught at load time, once.
    baddefault_choix = pathlib.Path(tmp) / "bc.yaml"
    baddefault_choix.write_text(
        "- key: edition\n  type: choix\n  label: X\n  choices: [a, b]\n"
        "  default: c\n")
    check_raises("a choix default outside its choices is refused at load time",
                 lambda: load_questions(str(baddefault_choix)), "edition")

    baddefault_bool = pathlib.Path(tmp) / "bb.yaml"
    baddefault_bool.write_text(
        "- key: retro\n  type: bool\n  label: X\n  default: \"yes\"\n")
    check_raises("a bool default that is a string is refused at load time",
                 lambda: load_questions(str(baddefault_bool)), "retro")

    # bool("false") is True: a package author writing `required: "false"`
    # must not silently get the opposite of what they typed.
    badrequired = pathlib.Path(tmp) / "br.yaml"
    badrequired.write_text(
        "- key: req_flag\n  type: bool\n  label: X\n  required: \"false\"\n")
    check_raises("a non-bool 'required' is refused rather than coerced",
                 lambda: load_questions(str(badrequired)), "req_flag")

    # `key: on` (unquoted) parses under YAML 1.1 as the bool True, not the
    # string "on" - a dict literal can't produce this, so it goes through a
    # real YAML file, the same trap manifest.py already refuses on dict keys.
    barekey = pathlib.Path(tmp) / "bk.yaml"
    barekey.write_text("- key: on\n  type: bool\n  label: X\n")
    check_raises("a bare on/off/yes/no key is refused, not coerced via str()",
                 lambda: load_questions(str(barekey)), "quote it")

# --- validate_answers ------------------------------------------------------ #
answers = validate_answers(questions, {
    "dedicated_disk": "/dev/nvme1n1",
    "admin_password": "hunter2hunter2",
    "edition": "pro",
})
check("answers pass through", answers["dedicated_disk"], "/dev/nvme1n1")
check("an unanswered optional takes its default", answers["retro"], False)
check("an answered optional wins over its default", answers["edition"], "pro")

check_raises("a missing required answer is refused",
             lambda: validate_answers(questions, {"admin_password": "x"}),
             "dedicated_disk")
check_raises("a choix outside its choices is refused",
             lambda: validate_answers(questions, {
                 "dedicated_disk": "/dev/nvme1n1", "admin_password": "x",
                 "edition": "home"}),
             "edition")
check_raises("a non-bool for a bool question is refused",
             lambda: validate_answers(questions, {
                 "dedicated_disk": "/dev/nvme1n1", "admin_password": "x",
                 "retro": "oui"}),
             "retro")
check_raises("an unknown key is refused",
             lambda: validate_answers(questions, {
                 "dedicated_disk": "/dev/nvme1n1", "admin_password": "x",
                 "sournois": 1}),
             "sournois")
check_raises("an empty-string answer to a required disque is refused",
             lambda: validate_answers(questions, {
                 "dedicated_disk": "", "admin_password": "x"}),
             "dedicated_disk")

check("a package with no questions accepts nothing",
      validate_answers([], {}), {})
check_raises("a package with no questions refuses any answer",
             lambda: validate_answers([], {"x": 1}), "x")

check("Question is frozen and comparable",
      Question("a", "bool", "A", False, (), False)
      == Question("a", "bool", "A", False, (), False), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all wizard vocabulary tests passed")
