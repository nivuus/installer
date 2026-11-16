"""Docker's own apt repository inside the target (the `docker` feature).

A fresh install gets Docker CE from download.docker.com, the way the
reference host runs it, not Debian's docker.io: Debian ships the Compose v2
plugin only from trixie on and in an older version, and the suite's packages
(home-manager, media-manager) declare no Docker apt name, they rely on this
feature for `docker compose`.

The setup follows Docker's documentation for Debian
(https://docs.docker.com/engine/install/debian/, read 2026-11-16): the key in
/etc/apt/keyrings/docker.asc, a deb822 source in
/etc/apt/sources.list.d/docker.sources naming it with Signed-By, the target's
codename and dpkg architecture, component `stable`.

The signing key is NOT fetched at install time. It is shipped in this
repository (templates/docker-archive-keyring.asc), fetched once on 2026-11-16
from https://download.docker.com/linux/debian/gpg. Its primary key
fingerprint is

    9DC8 5822 9FC7 DD38 854A  E2D8 8D81 803C 0EBF CD88
    "Docker Release (CE deb) <docker@docker.com>"

the one Docker's documentation published for this key (docker/docs), and the
same key the reference host trusts. scripts/tests/test_docker_repo.py fails if
the shipped file's fingerprint ever differs.
"""
from __future__ import annotations

import os
import shutil

from . import apt
from .util import StepError, chroot_run, write_file

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
KEY_SOURCE = os.path.join(TEMPLATES_DIR, "docker-archive-keyring.asc")
KEYRING = "etc/apt/keyrings/docker.asc"
SOURCES = "etc/apt/sources.list.d/docker.sources"
REPO_URI = "https://download.docker.com/linux/debian"

# Docker's documented package set for Debian.
DOCKER_APT = ["docker-ce", "docker-ce-cli", "containerd.io",
              "docker-buildx-plugin", "docker-compose-plugin"]


def target_architecture(target: str) -> str:
    """The target's dpkg architecture, asked to the target's own dpkg."""
    proc = chroot_run(target, ["dpkg", "--print-architecture"], check=False)
    arch = (getattr(proc, "stdout", "") or "").strip()
    if proc.returncode != 0 or not arch:
        raise StepError("feature docker: `dpkg --print-architecture` gave no "
                        "architecture in the target")
    return arch


def sources_entry(suite: str, arch: str) -> str:
    return (f"Types: deb\n"
            f"URIs: {REPO_URI}\n"
            f"Suites: {suite}\n"
            f"Components: stable\n"
            f"Architectures: {arch}\n"
            f"Signed-By: /{KEYRING}\n")


def install_docker_source(target: str, suite: str) -> None:
    """Lay Docker's key and source in the target, then refresh apt."""
    keyring = os.path.join(target, KEYRING)
    os.makedirs(os.path.dirname(keyring), mode=0o755, exist_ok=True)
    shutil.copyfile(KEY_SOURCE, keyring)
    os.chmod(keyring, 0o644)
    write_file(os.path.join(target, SOURCES),
               sources_entry(suite, target_architecture(target)))
    apt.update(target, f"feature docker (Docker's repository for {suite})")
