"""Rendering of the Apollo configuration and of the guest-side secrets file.

Apollo silently ignores a key it does not know, so a typo here costs a
streaming session with no HDR and no error anywhere. Every key rendered by
this module was read out of the Apollo 0.4.6 binary on 2026-08-22.

The Web-manager credentials are NOT rendered into sunshine.conf: Apollo hashes
them itself through `sunshine.exe --creds`, which is why this module ships them
separately in a PowerShell data file the guest steps read.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

STEAM_DIR = "D:\\Steam"
STATE_DIR = "D:\\state\\apollo"


class ApolloError(ValueError):
    """Raised when the Apollo configuration cannot be rendered safely."""


@dataclass(frozen=True)
class ApolloParams:
    ui_username: str
    ui_password: str
    steam_dir: str = STEAM_DIR
    state_dir: str = STATE_DIR


def _env() -> Environment:
    # No autoescape: these are a conf file and a JSON document, not HTML.
    return Environment(loader=FileSystemLoader(TEMPLATES_DIR),
                       keep_trailing_newline=True)


def render_conf(params: ApolloParams) -> str:
    # sunshine.conf carries no path and no secret: the config directory is a
    # junction to D:, so Apollo's own relative defaults already land there.
    return _env().get_template("sunshine.conf.j2").render()


def render_apps(params: ApolloParams) -> str:
    steam_exe = params.steam_dir.rstrip("\\") + "\\steam.exe"
    return _env().get_template("apps.json.j2").render(
        steam_exe=steam_exe.replace("\\", "\\\\"))


def render_secrets(admin_password: str, ui_username: str,
                   ui_password: str) -> str:
    """Render the .psd1 the guest steps read instead of taking arguments."""
    values = {"AdminPassword": admin_password,
              "ApolloUser": ui_username,
              "ApolloPassword": ui_password}
    for name, value in values.items():
        if not value:
            raise ApolloError(f"{name} must not be empty")
        # A single quote would close the PowerShell literal early: refuse it
        # rather than escape it, so no guest step can be made to run something
        # a secret smuggled in.
        if "'" in value or "\n" in value or "\r" in value:
            raise ApolloError(
                f"{name} must not contain a quote or a newline"
            )
    body = "\n".join(f"    {k} = '{v}'" for k, v in values.items())
    return "@{\n" + body + "\n}\n"
