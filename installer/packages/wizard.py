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
# Types the ENGINE fills from its hardware detection; the answer is a device
# path or a PCI slot the operator picked from a list the package never saw.
HARDWARE_TYPES = ("disque", "gpu")


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
        key = str(item.get("key") or "").strip()
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

        questions.append(Question(
            key=key, type=qtype, label=label, default=item.get("default"),
            choices=choices, required=bool(item.get("required", False)),
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
        if question.type == "bool" and not isinstance(value, bool):
            raise WizardError(
                f"question {question.key!r} expects true/false, got {value!r}")
        if question.type == "choix" and value not in question.choices:
            raise WizardError(
                f"question {question.key!r} expects one of {question.choices}, "
                f"got {value!r}")
        if question.type in ("texte", "secret") + HARDWARE_TYPES \
                and not isinstance(value, str):
            raise WizardError(
                f"question {question.key!r} expects a string, got {value!r}")
        validated[question.key] = value
    return validated
