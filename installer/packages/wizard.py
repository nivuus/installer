"""The restricted question vocabulary a package may add to the wizard.

Closed at six types rather than open at JSON Schema, on purpose. A third-party
package should not be able to draw arbitrary forms in the portal that the
operator is about to trust with their disk. And two of the six - `disque` and
`gpu` - exist precisely BECAUSE they need the engine's own hardware detection
to render: a package cannot draw a usable disk picker from inside its own
directory, and should not try.

Answers are validated here, once, before any of them reach a hook. A hook that
has to re-validate its own inputs is a hook that will forget to.
"""
from __future__ import annotations

from dataclasses import dataclass

import yaml

QUESTION_TYPES = ("bool", "choix", "texte", "secret", "disque", "gpu")
# The question whose answer is a whole block device the package claims for
# itself. Named because the ENGINE has to reason about it: only the engine
# knows the install target, so only the engine can refuse an answer that
# names it (see steps/packages.py).
DISK_TYPE = "disque"
# Types the ENGINE fills from its hardware detection; the answer is a device
# path or a PCI slot the operator picked from a list the package never saw.
HARDWARE_TYPES = (DISK_TYPE, "gpu")


class WizardError(RuntimeError):
    """Raised when a question file or a set of answers violates the contract."""


@dataclass(frozen=True)
class Question:
    key: str
    type: str
    label: str
    default: object = None
    choices: tuple[str, ...] = ()
    required: bool = False

    def to_dict(self) -> dict:
        """Shape sent to the portal. A secret never carries a default back."""
        payload = {"key": self.key, "type": self.type, "label": self.label,
                   "required": self.required}
        if self.choices:
            payload["choices"] = list(self.choices)
        if self.type != "secret" and self.default is not None:
            payload["default"] = self.default
        return payload


def _check_value(qtype: str, choices: tuple[str, ...], key: str, value,
                  context: str) -> None:
    """Type/constraint check shared by a question's own default (checked once,
    at load time, in load_questions) and an answer (checked in
    validate_answers). Applying the same rule in both places is what makes the
    module's claim true - "answers are validated once, here, before any of
    them reach a hook" - a bad default that skipped this check would reach a
    hook unchecked the moment the question went unanswered.
    """
    if qtype == "bool" and not isinstance(value, bool):
        raise WizardError(
            f"question {key!r} {context} expects true/false, got {value!r}")
    if qtype == "choix" and value not in choices:
        raise WizardError(
            f"question {key!r} {context} expects one of {choices}, "
            f"got {value!r}")
    if qtype in ("texte", "secret") + HARDWARE_TYPES \
            and not isinstance(value, str):
        raise WizardError(
            f"question {key!r} {context} expects a string, got {value!r}")


def load_questions(path: str) -> list[Question]:
    """Read and validate a package's question file."""
    try:
        with open(path) as fh:
            raw = yaml.safe_load(fh) or []
    except OSError as exc:
        raise WizardError(f"{path}: cannot be read ({exc})") from exc
    except yaml.YAMLError as exc:
        raise WizardError(f"{path}: invalid YAML ({exc})") from exc

    if not isinstance(raw, list):
        raise WizardError(f"{path}: must be a list of questions")

    questions: list[Question] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise WizardError(f"{path}: question #{index + 1} must be a mapping")

        # PyYAML implements YAML 1.1: a bare on/off/yes/no VALUE parses as a
        # bool, not a string - `key: on` yields True, not "on". manifest.py
        # hit the same trap on dict keys and refuses it; do the same here
        # instead of silently coercing via str(), which used to turn a
        # mistyped `key: on` into the literal key "True".
        raw_key = item.get("key")
        if raw_key is not None and not isinstance(raw_key, str):
            raise WizardError(
                f"{path}: question #{index + 1} key {raw_key!r} must be a "
                "string - quote it if it looks like on/off/yes/no, which "
                "YAML parses as a boolean")
        key = (raw_key or "").strip()
        if not key:
            raise WizardError(f"{path}: question #{index + 1} has no 'key'")
        if key in seen:
            raise WizardError(f"{path}: duplicate question key {key!r}")
        seen.add(key)

        qtype = str(item.get("type") or "").strip()
        if qtype not in QUESTION_TYPES:
            raise WizardError(
                f"{path}: question {key!r} has type {qtype!r}; expected one of "
                f"{QUESTION_TYPES}")

        label = str(item.get("label") or "").strip()
        if not label:
            raise WizardError(f"{path}: question {key!r} has no 'label'")

        choices = tuple(str(c) for c in (item.get("choices") or []))
        if qtype == "choix" and not choices:
            raise WizardError(
                f"{path}: question {key!r} is a 'choix' but declares no 'choices'")

        required_raw = item.get("required", False)
        if not isinstance(required_raw, bool):
            raise WizardError(
                f"{path}: question {key!r} 'required' must be true/false, "
                f"got {required_raw!r}")

        default = item.get("default")
        if default is not None:
            _check_value(qtype, choices, key, default, "default")

        questions.append(Question(
            key=key, type=qtype, label=label, default=default,
            choices=choices, required=required_raw,
        ))
    return questions


def validate_answers(questions, answers: dict) -> dict:
    """Validate `answers` against `questions`, filling in defaults.

    Unknown keys are refused rather than dropped: an answer the engine does
    not understand means the portal and the package disagree about the
    contract, and silently discarding it would hide that.
    """
    by_key = {q.key: q for q in questions}
    unknown = sorted(set(answers) - set(by_key))
    if unknown:
        raise WizardError(f"unknown answer keys: {', '.join(unknown)}")

    validated: dict = {}
    for question in questions:
        if question.key not in answers:
            if question.required:
                raise WizardError(
                    f"question {question.key!r} is required and unanswered")
            if question.default is not None:
                validated[question.key] = question.default
            continue

        value = answers[question.key]
        _check_value(question.type, question.choices, question.key, value,
                     "answer")
        # An empty device path / PCI slot reaching a hook is not a usable
        # answer for a required disque/gpu question.
        if question.type in HARDWARE_TYPES and question.required and value == "":
            raise WizardError(
                f"question {question.key!r} is required and cannot be an "
                "empty value")
        validated[question.key] = value
    return validated
