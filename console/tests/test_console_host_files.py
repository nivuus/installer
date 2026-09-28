#!/usr/bin/env python3
"""The repository carries the whole VM lifecycle, and carries no dead code.

Three failures this guards against, all already observed in this project:
a script that exists only as a deployed file on one host and is lost the
day that host is reinstalled (handle-vm-start.sh, until 2026-08-24),
placeholder hooks kept around long enough that documentation starts
promising them (the three hugepage stubs), and - the reverse of the first -
a hook VERSIONED here that no placement table ever deploys.

That last one is not hypothetical: the two real CPU wrappers
(10-cpu-confine.sh, 10-cpu-release.sh) sat in this tree for a month while
install.py wrote thinner heredocs over their destinations, so the public
nivuus-cpu-mode@ contract was honoured by nobody while the README claimed
the opposite. Nothing structural forbade it, which is why it is checked here.
"""
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONSOLE = os.path.join(ROOT, "console")

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)


# The idle half of wake-on-demand must be versioned, not merely deployed.
idle = os.path.join(CONSOLE, "host", "vm-idle-shutdown.sh")
check("vm-idle-shutdown.sh is versioned", os.path.isfile(idle))
if os.path.isfile(idle):
    head = open(idle).readline()
    check("vm-idle-shutdown.sh starts with a shebang", head.startswith("#!"))

for unit in ("vm-idle-shutdown.service", "vm-idle-shutdown.timer"):
    check(f"{unit} is versioned",
          os.path.isfile(os.path.join(CONSOLE, "host", "systemd", unit)))

# The three hugepage hooks were two-line no-ops that documentation kept
# promising. Naming them explicitly is the only non-arbitrary guard: the
# generic form of this check - "does this file do something useful" - has
# no computable definition, since `exit 0` is syntactically code.
removed = [
    "started/begin/00-set-hugepages.sh",
    "stopped/end/00-hugepages-fix.sh",
    "stopped/end/hugepages-reset.sh",
]
hooks_dir = os.path.join(CONSOLE, "host", "libvirt", "hooks", "qemu.d")
for rel in removed:
    path = os.path.join(hooks_dir, "Windows", rel)
    check(f"the removed stub {rel} has not come back", not os.path.exists(path))

# No placeholder hooks: a two-line no-op is worse than an absent file,
# because documentation reads the filename and promises behaviour.
for dirpath, _dirnames, filenames in os.walk(hooks_dir):
    for name in filenames:
        path = os.path.join(dirpath, name)
        body = [ligne for ligne in open(path).read().splitlines()
                if ligne.strip() and not ligne.strip().startswith("#")]
        rel = os.path.relpath(path, ROOT)
        check(f"{rel} does something", bool(body))

# Every versioned libvirt hook must be deployed by SOMETHING. The tables are
# read as real Python data, not scraped from the source text: install.py has
# no import side effects (module level is imports, constants and function
# definitions; the only call site is guarded by `if __name__ == "__main__"`),
# so loading it by path is both safe and exact where a regex over its f-string
# constants would be guesswork.
#
# Sources are collected from EVERY module-level placement table rather than
# from HOOK_FILES by name: the day a second table appears for these hooks,
# this check must follow it instead of crying wolf. A table is any list or
# tuple of (source, destination) string pairs; a lone source string
# (DROPIN_SRC) counts too.
INSTALL_HOOK = os.path.join(CONSOLE, "hooks", "install.py")
spec = importlib.util.spec_from_file_location("console_install", INSTALL_HOOK)
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


def placed_sources(module):
    sources = set()
    for name in dir(module):
        if name.startswith("__"):
            continue
        value = getattr(module, name)
        if isinstance(value, str) and value.startswith("host/"):
            sources.add(value)
            continue
        if not isinstance(value, (list, tuple)):
            continue
        for item in value:
            if (isinstance(item, (list, tuple)) and len(item) == 2
                    and all(isinstance(part, str) for part in item)):
                sources.add(item[0])
    return sources


deployed = placed_sources(install)
hooks_root = os.path.join(CONSOLE, "host", "libvirt", "hooks")
for dirpath, _dirnames, filenames in os.walk(hooks_root):
    for name in filenames:
        rel = os.path.relpath(os.path.join(dirpath, name), CONSOLE)
        # Name the orphan: this message is read by whoever just added a hook.
        check(f"{rel} est versionne mais aucune table de install.py ne le pose",
              rel in deployed)

# The vendored anti-detection patch MUST leave the virtio PCI vendor alone.
# Upstream renames 0x1af4 to 0x8086; the virtio-win drivers match on
# VEN_1AF4 and then never bind - measured 2026-09-15: guest up, NIC silent,
# virtiofs shares gone. host/qemu-anti-detection/README.md carries the full
# account; this assertion is what stops a re-vendoring from repeating it.
aa_dir = os.path.join(CONSOLE, "host", "qemu-anti-detection")
patches = [name for name in os.listdir(aa_dir) if name.endswith(".patch")]
check("exactly one vendored QEMU patch", len(patches) == 1)
for name in patches:
    body = open(os.path.join(aa_dir, name)).read()
    check(f"{name} keeps the virtio PCI vendor (no QUMRANET hunk)",
          "PCI_VENDOR_ID_REDHAT_QUMRANET" not in body)
    check(f"{name} keeps the virtio subsystem id (no PCI_SUBDEVICE_ID_QEMU hunk)",
          "PCI_SUBDEVICE_ID_QEMU" not in body)
build_script = os.path.join(aa_dir, "build-qemu.sh")
check("build-qemu.sh is versioned and executable",
      os.path.isfile(build_script) and os.access(build_script, os.X_OK))
# The two AppArmor site files must be deployed by a placement table too.
for rel in ("host/apparmor/local-usr.sbin.libvirtd",
            "host/apparmor/local-abstractions-libvirt-qemu"):
    check(f"{rel} est versionne et pose par install.py",
          os.path.isfile(os.path.join(CONSOLE, rel)) and rel in deployed)

# A hook may pause media-manager's containers and resume them - `stop` and
# `start`, which never recreate anything - but it must never converge that
# stack. The hooks address it with an explicit `-f docker-compose.yml`, and an
# explicit -f makes compose ignore the COMPOSE_FILE that media-manager writes
# into its .env to add its hardware overlay (docker-compose.qsv.yml: /dev/dri).
# Any recreate from a hook therefore drops the iGPU, and `up <service>` also
# converges that service's dependencies: rebind-host-gpu.sh ran
# `up -d tdarr-node-nvenc` on every VM shutdown, which could recreate `tdarr`
# itself without /dev/dri (2026-09-28 audit).
MEDIA_MANAGER = "/opt/nivuus/media-manager/"
CONVERGING = re.compile(r"\s(up|create|run)(\s|$)")
for dirpath, _dirnames, filenames in os.walk(hooks_root):
    for name in filenames:
        path = os.path.join(dirpath, name)
        rel = os.path.relpath(path, CONSOLE)
        # Join backslash continuations: 10-cpu-confine.sh spreads its compose
        # call over two lines.
        text = open(path).read().replace("\\\n", " ")
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if "docker compose" not in code or MEDIA_MANAGER not in code:
                continue
            verb = CONVERGING.search(code)
            check(f"{rel} only stops/starts media-manager containers, "
                  f"found `{verb.group(1) if verb else ''}`",
                  verb is None)

if failures:
    for item in failures:
        print(f"FAIL - {item}")
    sys.exit(1)
print("OK - the repository carries the full lifecycle, no placeholder "
      "hooks, and no hook that install.py forgets to deploy")
