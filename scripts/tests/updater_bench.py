"""A scratch machine for the updater suites: packages, releases, witnesses.

Every package here is real enough to run: a manifest the real parser loads
and hooks the real runner executes, published by a local fake GitHub. The
hooks leave two kinds of evidence, so what an update did is measured, never
assumed:
  - one line per call in a witness file (order and count of the hooks);
  - a file OUTSIDE the package directory, `<ETC>/<name>.conf`, rewritten by
    `install` with the version that wrote it - what a real hook does to the
    machine, and what a rollback has to put back.

Importing this module points every NIVUUS_* directory at a scratch root, so
it must be imported before `packages`.
"""
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
ROOT = tempfile.mkdtemp(prefix="nivuus-updater-")
WITNESS = os.path.join(ROOT, "witness.log")
ETC = os.path.join(ROOT, "etc/written-by-hooks")
for key, rel in (("NIVUUS_PACKAGES_DIR", "opt/nivuus-packages"),
                 ("NIVUUS_STATE_FILE", "etc/nivuus/packages.json"),
                 ("NIVUUS_STAMP_DIR", "var/lib/nivuus/packages"),
                 ("NIVUUS_CACHE_DIR", "var/cache/nivuus")):
    os.environ[key] = os.path.join(ROOT, rel)
os.environ["UPDATER_WITNESS"] = WITNESS
os.environ["UPDATER_ETC"] = ETC
os.makedirs(ETC)
sys.path.insert(0, str(REPO / "installer"))
sys.path.insert(0, str(HERE))

import fake_github  # noqa: E402

fake = fake_github.start()

from packages import state  # noqa: E402
from packages.updater import UpdateError  # noqa: E402

PACKAGES_DIR = os.environ["NIVUUS_PACKAGES_DIR"]
failures = []

# %s is the phase this version's hooks fail in ("" for none). The witness
# line and the file under ETC are written BEFORE the exit, the way a real
# hook fails: after it has already changed something.
HOOK = """import json, os, sys
phase = sys.argv[sys.argv.index("--phase") + 1]
ctx = json.load(sys.stdin)
name, version = ctx["package"]["name"], ctx["package"]["version"]
with open(os.environ["UPDATER_WITNESS"], "a") as fh:
    fh.write(f"{name} {phase} {version} "
             f"{json.dumps(ctx['answers'], sort_keys=True)}\\n")
if phase == "install":
    with open(os.path.join(os.environ["UPDATER_ETC"], name + ".conf"), "w") as fh:
        fh.write(version + "\\n")
sys.exit(1 if phase == %r else 0)
"""


def check_eq(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_refused(label, fn, needle):
    try:
        fn()
    except UpdateError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        return
    failures.append(f"{label}: expected UpdateError, none raised")


def manifest_text(name, version, *, source=True, requires=(), questions=False,
                  features=(), capabilities=(), apt=()):
    lines = ["apiVersion: nivuus.dev/v1", f"name: {name}",
             f"version: {version}", f"label: {name}", "tier: userspace",
             "hooks:", "  install: hooks/install.py",
             "  activate: hooks/activate.py"]
    if source:
        lines += ["source:", f"  github: nivuus/{name}"]
    required = [(key, values) for key, values in (
        ("packages", requires), ("features", features),
        ("capabilities", capabilities)) if values]
    if required:
        lines += ["requires:"] + [f"  {key}: [{', '.join(values)}]"
                                  for key, values in required]
    if apt:
        lines += [f"apt: [{', '.join(apt)}]"]
    if questions:
        lines += ["wizard:", "  questions: wizard.yaml"]
    return "\n".join(lines) + "\n"


def package_files(name, version, *, fail="", **kwargs):
    """`fail` names the phase whose hook exits 1: "install" or "activate"."""
    files = {"nivuus-package.yaml": manifest_text(name, version, **kwargs),
             "hooks/install.py": HOOK % fail,
             "hooks/activate.py": HOOK % fail}
    if kwargs.get("questions"):
        files["wizard.yaml"] = ("- key: zone\n  label: Zone\n  type: texte\n"
                                "  required: true\n")
    return files


def install_locally(name, version, answers=None, **kwargs):
    """What the engine leaves behind: a directory and a state record."""
    dest = os.path.join(PACKAGES_DIR, name)
    for rel, content in package_files(name, version, **kwargs).items():
        path = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)
    current = state.load()
    current[name] = {"version": version}
    if answers is not None:
        current[name]["answers"] = answers
    state.save(current)


def publish(name, version, **kwargs):
    archive_name = kwargs.pop("archive_name", name)
    files = package_files(archive_name, kwargs.pop("manifest_version", version),
                          **kwargs)
    fake.publish(f"nivuus/{name}", version, fake_github.build_archive(files))


def witness():
    if not os.path.exists(WITNESS):
        return []
    with open(WITNESS) as fh:
        return fh.read().splitlines()


def installed_version(name):
    with open(os.path.join(PACKAGES_DIR, name, "nivuus-package.yaml")) as fh:
        return [line.split(": ")[1] for line in fh.read().splitlines()
                if line.startswith("version:")][0]


def written_by_hooks(name):
    """The version whose `install` last wrote outside the package directory."""
    path = os.path.join(ETC, name + ".conf")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return fh.read().strip()


def hw():
    return {}


def finish(what):
    fake.close()
    if failures:
        print(f"FAIL ({len(failures)})")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print(f"OK - all {what} tests passed")
