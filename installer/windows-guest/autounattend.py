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

DISK_MODES = ("wipe", "rebuild")
# The GAMES partition is the one with a fixed size now, and Windows takes
# whatever is left - the opposite of the layout used until 2026-08-25. That
# reversal is forced by putting the data partition FIRST (see the template's
# comment for why it must come first): <Extend> only applies to the last
# partition created, so whichever partition sits first has to be sized.
#
# 140 GiB holds a comfortable Steam library. The floor matches the one
# 20-disk.ps1 enforces from inside the guest, so a typo is refused here rather
# than discovered forty minutes into an install.
DEFAULT_DATA_PARTITION_MB = 143360
MIN_DATA_PARTITION_MB = 102400


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
    # The NVIDIA driver (and any other stage that requests one) reboots the
    # guest mid-provisioning, so this must count high enough for automatic
    # logon to survive every reboot before run-all.ps1 reaches 50-power.ps1,
    # which makes autologon permanent. Unlike sub-project A, B never turns
    # autologon back off: 99-marker.ps1 deliberately keeps it on forever,
    # since Apollo needs a permanently open interactive session to capture.
    autologon_count: int = 5
    # "wipe" partitions the whole disk; "rebuild" reformats C: and leaves the
    # games partition alone. Rebuild is safe only when run deliberately against
    # a disk previously partitioned by this tool. 20-disk.ps1 detects a wrong
    # target post-install via D:\state\NIVUUS-DATA.id, but cannot prevent the
    # reformat (runs after Setup's windowsPE pass). Real guard is in build.py.
    disk_mode: str = "wipe"
    # Size in MB of the games partition. Validated even in rebuild mode (where
    # the template ignores it), as a typo guard; the error message does not
    # distinguish the two modes.
    data_partition_mb: int = DEFAULT_DATA_PARTITION_MB


def validate(params: UnattendParams) -> None:
    if not KEY_RE.match(params.product_key):
        raise UnattendError(
            "product key must be XXXXX-XXXXX-XXXXX-XXXXX-XXXXX format (5 groups of 5 "
            "uppercase alphanumeric characters separated by dashes)"
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
    if params.disk_mode not in DISK_MODES:
        raise UnattendError(
            f"disk_mode must be one of {DISK_MODES}, got {params.disk_mode!r}"
        )
    if params.data_partition_mb < MIN_DATA_PARTITION_MB:
        raise UnattendError(
            f"data partition must be at least {MIN_DATA_PARTITION_MB} MB, "
            f"got {params.data_partition_mb}"
        )


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
        disk_mode=params.disk_mode,
        data_partition_mb=params.data_partition_mb,
    )
