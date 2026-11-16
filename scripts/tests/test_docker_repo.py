#!/usr/bin/env python3
"""Tests for the docker feature: Docker CE from Docker's own apt repository.

The engine runs no command here: chroot_run is replaced by a fake that
records each call and answers like the target would.

Run: python3 scripts/tests/test_docker_repo.py
"""
import base64
import hashlib
import pathlib
import stat
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer"))

from steps import docker_repo, features  # noqa: E402

failures = []

# Docker's release key, as Docker's documentation published it. Changing the
# shipped key file changes this: it is the point of the test.
DOCKER_KEY_FINGERPRINT = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


class FakeEmit:
    def info(self, step, pct, msg):
        pass

    warn = error = info


def openpgp_primary_fingerprint(armored: str) -> str:
    """v4 fingerprint of the first public-key packet (RFC 4880 12.2)."""
    lines = armored.strip().splitlines()
    body = lines[lines.index("") + 1:]
    data = base64.b64decode("".join(line for line in body
                                    if not line.startswith(("=", "-----"))))
    tag = data[0]
    if tag & 0x40:  # new-format packet header
        if (tag & 0x3F) != 6:
            raise ValueError("first packet is not a public key")
        first = data[1]
        if first < 192:
            length, start = first, 2
        elif first < 224:
            length, start = ((first - 192) << 8) + data[2] + 192, 3
        else:
            length, start = int.from_bytes(data[2:6], "big"), 6
    else:  # old-format packet header
        if ((tag >> 2) & 0x0F) != 6:
            raise ValueError("first packet is not a public key")
        size = {0: 1, 1: 2, 2: 4}[tag & 0x03]
        length = int.from_bytes(data[1:1 + size], "big")
        start = 1 + size
    packet = data[start:start + length]
    if packet[0] != 4:
        raise ValueError(f"unsupported key version {packet[0]}")
    digest = hashlib.sha1(b"\x99" + len(packet).to_bytes(2, "big") + packet)
    return digest.hexdigest().upper()


check("the shipped key is Docker's release key",
      openpgp_primary_fingerprint(pathlib.Path(docker_repo.KEY_SOURCE).read_text()),
      DOCKER_KEY_FINGERPRINT)

calls = []


def runner(arch="arm64", fail_on=None, code=100):
    """A fake chroot_run; the call containing `fail_on` fails."""
    def fake(target, cmd, **kwargs):
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = arch + "\n" if cmd[0] == "dpkg" else ""
            stderr = ""
        if fail_on and fail_on in " ".join(cmd):
            R.returncode = code
            R.stderr = "E: some apt diagnosis"
            if kwargs.get("check", True):
                raise features.StepError(f"command failed ({code}): {' '.join(cmd)}")
        return R()
    return fake


def install(config, fake):
    calls.clear()
    features.chroot_run = features.apt.chroot_run = docker_repo.chroot_run = fake
    tmp = tempfile.mkdtemp()
    features.apply_features(config, tmp, "/opt/nivuus", {}, FakeEmit())
    return pathlib.Path(tmp)


# bookworm stays selectable; the source names the suite and the target's arch.
target = install({"features": ["os-base", "docker"], "suite": "bookworm"}, runner())
sources = (target / docker_repo.SOURCES).read_text()
check("deb822 source as Docker documents it", sources,
      "Types: deb\nURIs: https://download.docker.com/linux/debian\n"
      "Suites: bookworm\nComponents: stable\nArchitectures: arm64\n"
      "Signed-By: /etc/apt/keyrings/docker.asc\n")
keyring = target / docker_repo.KEYRING
check("the key installed is the shipped one, not a download",
      keyring.read_bytes(), pathlib.Path(docker_repo.KEY_SOURCE).read_bytes())
check("the key is world-readable for apt", stat.S_IMODE(keyring.stat().st_mode), 0o644)
check("nothing is downloaded by the engine itself",
      [c for c in calls if c[0] in ("curl", "wget", "gpg")], [])
check("order: arch, update, install, compose check, enable",
      [c for c in calls if c[0] in ("dpkg", "apt-get", "docker", "systemctl")],
      [["dpkg", "--print-architecture"], ["apt-get", "update"],
       ["apt-get", "install", "-y", "docker-ce", "docker-ce-cli", "containerd.io",
        "docker-buildx-plugin", "docker-compose-plugin"],
       ["docker", "compose", "version"], ["systemctl", "enable", "docker"]])

# No suite in the config: the engine's default, trixie.
target = install({"features": ["os-base", "docker"]}, runner(arch="amd64"))
check("default suite is trixie",
      "Suites: trixie\n" in (target / docker_repo.SOURCES).read_text(), True)


def fails(label, config, fake, needle, names_feature=True):
    try:
        install(config, fake)
        failures.append(f"{label}: no StepError")
        return
    except features.StepError as exc:
        check(f"{label}: the error names {needle!r}", needle in str(exc), True)
        if names_feature:
            check(f"{label}: the error names the feature",
                  "feature docker" in str(exc), True)


cfg = {"features": ["os-base", "docker"]}
fails("an unreachable repository fails the install", cfg,
      runner(fail_on="apt-get update"), "apt-get update failed")
check("no install after a failed update",
      [c for c in calls if c[:2] == ["apt-get", "install"]], [])
fails("a target without an architecture fails", cfg, runner(arch=""),
      "--print-architecture")
fails("a package apt cannot install fails the install", cfg,
      runner(fail_on="docker-compose-plugin"), "some apt diagnosis")
fails("a missing compose plugin fails the install", cfg,
      runner(fail_on="docker compose version", code=1), "docker compose version")
fails("docker that cannot be enabled fails the install", cfg,
      runner(fail_on="enable docker", code=1), "enable docker",
      names_feature=False)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all docker repository tests passed")
