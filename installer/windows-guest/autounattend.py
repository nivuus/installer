"""Validation and rendering of the Windows 11 LTSC unattended answer file.

Pure logic: no subprocess, no writes outside the caller's hands, so the whole
module is testable without a Windows medium.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

KEY_RE = re.compile(r"^[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}$")
# NetBIOS name: 15 characters maximum, letters, digits and hyphens.
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,14}$")

# The built-in administrator account of an en-US installation. A French
# medium would name it "Administrateur"; targeting the wrong name makes the
# automatic logon fail silently, and with it every session-1 provisioning step.
ADMIN_ACCOUNT = "Administrator"

# The install medium letter is unpredictable, so the guest scans every drive
# for this marker instead of assuming D:.
PAYLOAD_MARKER = r"\nivuus\PAYLOAD.id"
DRIVE_LETTERS = "C D E F G H I J K L M N O P Q R S T U V W X Y Z"


class UnattendError(ValueError):
    """Raised when answer-file parameters cannot produce a valid install."""


@dataclass(frozen=True)
class UnattendParams:
    product_key: str
    admin_password: str
    image_name: str
    hostname: str = "NIVUUS-WIN"
    # The LTSC 2024 medium is en-US only, so Setup itself must stay en-US;
    # only the keyboard and regional formats become French.
    setup_language: str = "en-US"
    user_locale: str = "fr-FR"
    # The NVIDIA driver reboots, so provisioning must survive at least two
    # extra logons before the automatic logon is turned off again.
    autologon_count: int = 5


def validate(params: UnattendParams) -> None:
    if not KEY_RE.match(params.product_key):
        raise UnattendError(
            "product key must look like XXXXX-XXXXX-XXXXX-XXXXX-XXXXX, "
            f"got {params.product_key!r}"
        )
    if not params.admin_password:
        raise UnattendError("administrator password must not be empty")
    if not HOSTNAME_RE.match(params.hostname):
        raise UnattendError(
            f"hostname must be 1-15 chars of [A-Za-z0-9-], got {params.hostname!r}"
        )
    if not params.image_name.strip():
        raise UnattendError("image name must not be empty")
    if params.autologon_count < 3:
        raise UnattendError("autologon count must be >= 3: provisioning reboots")


def render(params: UnattendParams, templates_dir: str = TEMPLATES_DIR) -> str:
    """Render the answer file. Values are XML-escaped; the template is not."""
    validate(params)
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=select_autoescape(enabled_extensions=("j2",), default=True),
        keep_trailing_newline=True,
    )
    return env.get_template("autounattend.xml.j2").render(
        product_key=params.product_key,
        admin_password=params.admin_password,
        image_name=params.image_name,
        hostname=params.hostname,
        setup_language=params.setup_language,
        user_locale=params.user_locale,
        admin_account=ADMIN_ACCOUNT,
        autologon_count=params.autologon_count,
        drive_letters=DRIVE_LETTERS,
        payload_marker=PAYLOAD_MARKER,
    )
