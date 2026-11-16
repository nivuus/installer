#!/usr/bin/env python3
"""Tests for the shipped Docker daemon.json (steps/docker_config.py).

Real merge/render/install functions, real files in a temp directory; only the
chroot commands of the docker feature are faked.

Run: python3 scripts/tests/test_docker_daemon_config.py
"""
import json
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer"))

from steps import docker_config, features  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


class FakeEmit:
    def __init__(self):
        self.lines = []

    def info(self, step, pct, msg):
        self.lines.append(("info", msg))

    def warn(self, step, pct, msg):
        self.lines.append(("warn", msg))

    def error(self, step, pct, msg):
        self.lines.append(("error", msg))


template = docker_config.load_template()
check("template caps log files", template["log-opts"], {"max-size": "50m", "max-file": "3"})
check("template enables builder gc", template["builder"]["gc"]["enabled"], True)
check("template does not decide iptables", "iptables" in template, False)

# Fresh install: the template alone.
with tempfile.TemporaryDirectory() as tmp:
    emit = FakeEmit()
    check("fresh install writes", docker_config.install_daemon_config(tmp, emit), True)
    path = pathlib.Path(tmp) / "etc/docker/daemon.json"
    check("fresh content is the template", json.loads(path.read_text()), template)
    check("file mode 0644", oct(path.stat().st_mode & 0o777), "0o644")
    check("no temp file left", [p.name for p in path.parent.iterdir()], ["daemon.json"])
    # Idempotent: a second run changes nothing.
    check("second run writes nothing", docker_config.install_daemon_config(tmp, emit), False)

# Existing file: its values win, missing ones are filled, unknown keys survive.
existing = {"iptables": False, "log-opts": {"max-size": "10m"},
            "builder": {"gc": {"defaultKeepStorage": "5GB"}}, "live-restore": True}
with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "etc/docker/daemon.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(existing))
    docker_config.install_daemon_config(tmp, FakeEmit())
    got = json.loads(path.read_text())
    check("existing iptables kept", got["iptables"], False)
    check("existing live-restore kept", got["live-restore"], True)
    check("existing max-size wins, max-file filled", got["log-opts"],
          {"max-size": "10m", "max-file": "3"})
    check("nested gc merged, existing keep storage wins", got["builder"]["gc"],
          {"defaultKeepStorage": "5GB", "enabled": True})
    check("log driver filled", got["log-driver"], "json-file")

# Another log driver: json-file options must not be added to it.
merged = docker_config.merge_daemon_config({"log-driver": "journald"}, template)
check("journald keeps no json-file opts", "log-opts" in merged, False)
check("journald driver kept", merged["log-driver"], "journald")

# Unreadable existing file: left byte for byte, reported, never overwritten.
for label, content in (("invalid json", "{not json"), ("array", "[1, 2]")):
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "etc/docker/daemon.json"
        path.parent.mkdir(parents=True)
        path.write_text(content)
        emit = FakeEmit()
        check(f"{label}: not written", docker_config.install_daemon_config(tmp, emit), False)
        check(f"{label}: untouched", path.read_text(), content)
        check(f"{label}: warned", [k for k, _ in emit.lines], ["warn"])

# A failing write must not leave a partial daemon.json or a stray temp file.
with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "etc/docker/daemon.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"live-restore": true}')
    real_replace = os.replace

    def boom(src, dst):
        raise OSError("disk full")
    os.replace = boom
    try:
        try:
            docker_config.install_daemon_config(tmp, FakeEmit())
            raised = False
        except OSError:
            raised = True
    finally:
        os.replace = real_replace
    check("write failure propagates", raised, True)
    check("original intact after failure", path.read_text(), '{"live-restore": true}')
    check("no temp file after failure", [p.name for p in path.parent.iterdir()], ["daemon.json"])

# Wired into the docker feature. The fake runner follows the engine's
# contract (a result with a returncode, apt included); it records whether
# daemon.json was already on disk when the engine enabled docker.
enabled_with_config = []


def fake_chroot_run(target, cmd, **kwargs):
    if cmd == ["systemctl", "enable", "docker"]:
        enabled_with_config.append(
            (pathlib.Path(target) / "etc/docker/daemon.json").exists())

    class R:
        returncode = 0
        stdout = stderr = ""
    return R()


features.chroot_run = features.apt.chroot_run = fake_chroot_run
with tempfile.TemporaryDirectory() as tmp:
    features.apply_features({"features": ["docker"]}, tmp, "/opt/nivuus", {}, FakeEmit())
    check("docker feature ships daemon.json",
          (pathlib.Path(tmp) / "etc/docker/daemon.json").exists(), True)
    check("daemon.json is in place before docker is enabled",
          enabled_with_config, [True])
with tempfile.TemporaryDirectory() as tmp:
    features.apply_features({"features": ["os-base"]}, tmp, "/opt/nivuus", {}, FakeEmit())
    check("no docker feature, no daemon.json",
          (pathlib.Path(tmp) / "etc/docker/daemon.json").exists(), False)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all docker daemon.json tests passed")
