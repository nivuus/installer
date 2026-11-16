"""Locales, keyboard layouts and time zones a Debian system actually offers.

Read from the files Debian itself ships, never from a list written here: a
list in the code is a list of the languages someone thought of.

  * locales: /usr/share/i18n/SUPPORTED (package `locales`), one
    `<name> <charset>` per line; only the UTF-8 entries are offered.
  * keyboard layouts and variants: /usr/share/X11/xkb/rules/base.lst
    (package `xkb-data`), the same list keyboard-configuration offers.
  * time zones: /usr/share/zoneinfo (package `tzdata`).

The portal reads them on the live medium to build its choices; the install
engine reads them in the target to refuse a value the target cannot apply.
Standard library only.
"""
from __future__ import annotations

import os
import re

SUPPORTED = "usr/share/i18n/SUPPORTED"
XKB_BASE_LST = "usr/share/X11/xkb/rules/base.lst"
ZONEINFO = "usr/share/zoneinfo"

# Structural checks for values that end up in configuration files. They let
# through every name the files above use and nothing that could break a line.
LOCALE_NAME = re.compile(r"[A-Za-z0-9_.@-]{1,64}")
XKB_NAME = re.compile(r"[A-Za-z0-9_()-]{0,64}")
TIMEZONE_NAME = re.compile(r"[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*")


def utf8_locales(supported_text: str) -> dict[str, str]:
    """`{locale: charset}` for every UTF-8 entry of a SUPPORTED file."""
    found = {}
    for line in supported_text.splitlines():
        fields = line.split()
        if len(fields) == 2 and not line.startswith("#") and fields[1] == "UTF-8":
            found[fields[0]] = fields[1]
    return found


def xkb_layouts(base_lst_text: str) -> dict[str, dict]:
    """`{layout: {"description": str, "variants": {variant: description}}}`."""
    layouts: dict[str, dict] = {}
    section = None
    for line in base_lst_text.splitlines():
        if line.startswith("!"):
            section = line[1:].strip()
            continue
        fields = line.split(None, 1)
        if len(fields) != 2:
            continue
        name, description = fields[0], fields[1].strip()
        if section == "layout":
            layouts[name] = {"description": description, "variants": {}}
        elif section == "variant":
            layout, sep, label = description.partition(":")
            if sep and layout in layouts:
                layouts[layout]["variants"][name] = label.strip()
    return layouts


def _read(root: str, relative: str) -> str | None:
    try:
        with open(os.path.join(root, relative), encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def locales_of(root: str) -> dict[str, str] | None:
    """UTF-8 locales offered under `root`, or None when SUPPORTED is absent."""
    text = _read(root, SUPPORTED)
    return None if text is None else utf8_locales(text)


def layouts_of(root: str) -> dict[str, dict] | None:
    """XKB layouts offered under `root`, or None when base.lst is absent."""
    text = _read(root, XKB_BASE_LST)
    return None if text is None else xkb_layouts(text)


def timezone_exists(root: str, name: str) -> bool:
    if not TIMEZONE_NAME.fullmatch(name):
        return False
    return os.path.isfile(os.path.join(root, ZONEINFO, name))


def choices(root: str = "/") -> dict:
    """What the portal offers, with the name of any list it could not read.

    A missing file is reported, not papered over with a short default list:
    the portal then lets the operator type the value instead.
    """
    locales = locales_of(root)
    layouts = layouts_of(root)
    missing = [path for path, value in ((SUPPORTED, locales), (XKB_BASE_LST, layouts))
               if value is None]
    return {
        "locales": sorted(locales or {}),
        "keyboards": [{"layout": name, "description": data["description"],
                       "variants": [{"variant": v, "description": d}
                                    for v, d in sorted(data["variants"].items())]}
                      for name, data in sorted((layouts or {}).items())],
        "missing": ["/" + path for path in missing],
    }
