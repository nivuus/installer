#!/usr/bin/env python3
"""Every Python dependency is pinned, and the CI and the live image agree.

requirements.txt is what CI installs; installer/webapp/requirements.txt is
what the live image's venv installs. An unpinned line makes a run depend on
the day it runs; two pins of one package make CI test something the medium
does not ship.

Run: python3 scripts/tests/test_requirements_pins.py
"""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
LINE = re.compile(r"^([A-Za-z0-9_.-]+)(\[[^\]]*\])?\s*(\S.*)?$")

failures = []


def pins(path):
    found = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, spec = LINE.match(line).groups()
        if not (spec or "").startswith("=="):
            failures.append(f"{path.relative_to(REPO)}: {name} is not pinned with == ({spec!r})")
        found[name.lower().replace("_", "-")] = spec
    return found


ci = pins(REPO / "requirements.txt")
live = pins(REPO / "installer" / "webapp" / "requirements.txt")
for name in sorted(ci.keys() & live.keys()):
    if ci[name] != live[name]:
        failures.append(f"{name}: requirements.txt says {ci[name]}, "
                        f"installer/webapp/requirements.txt says {live[name]}")
missing = sorted(live.keys() - ci.keys())
if missing:
    failures.append(f"the live image installs what CI does not test: {missing}")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all requirement pins agree")
