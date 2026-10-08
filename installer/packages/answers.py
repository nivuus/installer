"""Recording the wizard answers of an installed package, after the fact.

The engine records a package's answers at install time; the updater replays
them. A package that was ADOPTED has none, so its update is refused - and
this module is the way out: the operator states the answers once, they are
validated against the package's own questions with the same wizard rules the
portal uses, and only then written to the state file (0600).

Values arrive as `key=value` strings and are typed by their question: a
`bool` question accepts true/false/yes/no/1/0 and nothing else. A required
`secret` that is still missing is asked for on the terminal, without echo,
twice; with no terminal the command refuses and names it - a secret is never
expected on the command line, where it would land in the shell history.

A secret already recorded is kept by later calls, so a wrong one used to be
permanent: nothing could ask for it again (the reference host's console,
2026-10-08, recorded an admin password its guest rejects). `key=` with an
EMPTY value names a secret to ask again on the terminal - the value itself
still never travels on argv.
"""
from __future__ import annotations

import getpass
import os
import sys

from . import state as state_mod
from .discovery import PACKAGES_DIR
from .manifest import MANIFEST_NAME, ManifestError, load_manifest
from .updater import UpdateError, lock
from .wizard import WizardError, load_questions, validate_answers

TRUE_WORDS = ("true", "yes", "1")
FALSE_WORDS = ("false", "no", "0")


def _questions(name: str):
    try:
        manifest = load_manifest(os.path.join(PACKAGES_DIR, name, MANIFEST_NAME))
    except ManifestError as exc:
        raise UpdateError(f"{name}: {exc}") from exc
    if not manifest.questions_file:
        return []
    try:
        return load_questions(os.path.join(manifest.root, manifest.questions_file))
    except WizardError as exc:
        raise UpdateError(f"{name}: {exc}") from exc


def parse_assignments(questions, assignments: list[str]) -> tuple[dict, set]:
    """`key=value` strings typed by their question.

    Returns the typed values and the secrets named with an empty value,
    i.e. to be asked again on the terminal.
    """
    by_key = {q.key: q for q in questions}
    parsed = {}
    reask = set()
    for assignment in assignments:
        key, sep, raw = assignment.partition("=")
        if not sep:
            raise UpdateError(f"expected key=value, got {assignment!r}")
        question = by_key.get(key)
        if question is None:
            raise UpdateError(f"unknown question {key!r}; this package asks: "
                              f"{', '.join(sorted(by_key)) or 'nothing'}")
        if question.type == "secret":
            if raw == "":
                reask.add(key)
                continue
            raise UpdateError(f"{key!r} is a secret: leave it off the command "
                              f"line, it is asked for on the terminal ({key}= "
                              "with no value asks for it again)")
        if question.type == "bool":
            word = raw.strip().lower()
            if word not in TRUE_WORDS + FALSE_WORDS:
                raise UpdateError(f"{key!r} expects true or false, got {raw!r}")
            parsed[key] = word in TRUE_WORDS
        else:
            parsed[key] = raw
    return parsed, reask


def _ask_secret(question, prompt=getpass.getpass) -> str:
    first = prompt(f"{question.label} ({question.key}): ")
    if not first:
        raise UpdateError(f"{question.key!r} is required and was left empty")
    if prompt("Again, to confirm: ") != first:
        raise UpdateError(f"{question.key!r}: the two entries differ")
    return first


def record(name: str, assignments: list[str], *, interactive=None,
           prompt=getpass.getpass) -> dict:
    """Merge `assignments` into `name`'s answers, validate, save.

    Returns the validated answers. `interactive` defaults to whether stdin
    is a terminal; tests pass it explicitly with their own `prompt`.
    """
    if interactive is None:
        interactive = sys.stdin.isatty()
    with lock():
        current = state_mod.load()
        if name not in current:
            raise UpdateError(f"{name}: not installed on this machine")
        questions = _questions(name)
        answers = dict(current[name].get("answers") or {})
        given, reask = parse_assignments(questions, assignments)
        answers.update(given)
        missing = [q for q in questions if q.type == "secret"
                   and (q.key in reask or (q.required and not answers.get(q.key)))]
        if missing and not interactive:
            raise UpdateError(
                f"{name}: {', '.join(q.key for q in missing)} must be entered on "
                "a terminal (run this command from an interactive shell)")
        for question in missing:
            answers[question.key] = _ask_secret(question, prompt)
        try:
            validated = validate_answers(questions, answers)
        except WizardError as exc:
            raise UpdateError(f"{name}: {exc}") from exc
        current[name]["answers"] = validated
        state_mod.save(current)
        return validated


def masked(name: str, answers: dict) -> dict:
    """`answers` with every secret replaced, for display."""
    secrets = {q.key for q in _questions(name) if q.type == "secret"}
    return {k: ("<secret>" if k in secrets else v) for k, v in answers.items()}
