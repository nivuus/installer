#!/usr/bin/env python3
"""The wizard's words live in catalogs, English being the reference.

Checks the contract between wizard.html / the scripts and the catalogs:
every visible text of the page is a catalog key, every key the page or the
scripts use exists in en.json, and no other catalog has a key or a
placeholder English lacks. The runtime side (negotiation, fallback) is
driven for real in scripts/tests/test_webapp_i18n.mjs under node.

Run: python3 scripts/tests/test_webapp_i18n.py
"""
import html.parser
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
WEBAPP = REPO / "installer" / "webapp"
CATALOGS = WEBAPP / "static" / "i18n"

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


en = json.loads((CATALOGS / "en.json").read_text(encoding="utf-8"))


class Page(html.parser.HTMLParser):
    """Collect the keys and every text node not owned by a data-i18n element."""
    VOID = {"input", "meta", "link", "br", "img"}

    def __init__(self):
        super().__init__()
        self.stack, self.keys, self.loose = [], set(), []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for name in ("data-i18n", "data-i18n-placeholder"):
            if name in attrs:
                self.keys.add(attrs[name])
        if tag not in self.VOID:
            self.stack.append((tag, "data-i18n" in attrs))

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        text = data.strip()
        if not text or any(tag in ("script", "style") for tag, _ in self.stack):
            return
        if not any(owned for _, owned in self.stack):
            self.loose.append(text)


page = Page()
page.feed((WEBAPP / "templates" / "wizard.html").read_text(encoding="utf-8"))
# The brand and a number are not words to translate.
check("no visible text outside the catalogs",
      [t for t in page.loose if t not in ("NIVUUS", "0 %")], [])
check("every key the page uses exists in en.json", sorted(page.keys - en.keys()), [])

scripts = "".join((WEBAPP / "static" / name).read_text(encoding="utf-8")
                  for name in ("app.js", "regional.js", "i18n.js"))
used = set(re.findall(r'\bt\("([a-z0-9_.-]+)"', scripts))
steps = re.search(r"const STEPS = \[([^\]]*)\]", scripts).group(1)
used |= {f"steps.{s}" for s in re.findall(r'"([a-z]+)"', steps)}
for key in re.findall(r'\{ key: "([a-z-]+)"', scripts):
    used |= {f"features.{key}.label", f"features.{key}.desc"}
for code in ("demo_mode", "already_running"):  # main.py's `code` values
    used.add(f"error.code.{code}")
check("every key the scripts use exists in en.json", sorted(used - en.keys()), [])
check("no unused key in en.json", sorted(en.keys() - used - page.keys), [])

placeholder = re.compile(r"\{([a-z_]+)\}")
for path in sorted(CATALOGS.glob("*.json")):
    catalog = json.loads(path.read_text(encoding="utf-8"))
    check(f"{path.name}: no key English lacks", sorted(catalog.keys() - en.keys()), [])
    for key, text in catalog.items():
        if key in en:
            check(f"{path.name}:{key}: same placeholders as English",
                  sorted(placeholder.findall(text)), sorted(placeholder.findall(en[key])))

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all wizard catalog tests passed")
