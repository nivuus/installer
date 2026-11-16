"""The desk agent, as the `desk` package publishes it: where, and how it is checked.

`agent.exe` is the Windows agent of `nivuus/desk`. It has ONE owner, `desk`,
which builds it and publishes it in its own deployed tree:

    /opt/nivuus/desk/agent/agent.exe
    /opt/nivuus/desk/agent/agent.manifest.json

`/opt/nivuus/<package>/` is where the suite already reaches into another
package's deployed files (`home-stock` and `home-desk` write into
`/opt/nivuus/home-manager/config`). It is not `/opt/nivuus-packages/<name>`,
which the updater replaces wholesale at every update of that package, and not
`/var/lib/nivuus-desk`, which the network-facing desk service can write.

This package only CONSUMES it, and only at the moment it stages a guest
payload (`guest/fetch_payload.py`, `guest/build.py`, the `payload` predicate
of `guest_steps.py`). Reading at the point of use is what makes the two
packages converge without any ordering between their updates: nothing of the
agent lives in this package's tree, so updating `console` cannot lose or
revert it, and the next payload staged after `desk` republished carries the
new binary.

Three states, and no fourth:

  * nothing published and `desk` not installed - `published_agent()` returns
    None. A console without desk is a valid machine: the caller skips the
    agent, and says so.
  * a manifest is there - it is parsed strictly and returned; `verify()` then
    refuses a binary that is not the one the manifest describes.
  * anything else (`desk` installed with no manifest, a binary with no
    manifest, an unreadable manifest) - DeskAgentError. Never a fallback to
    some other binary.

The manifest format and the path are a contract with `nivuus/desk`
(`hooks/agent_payload.py` there). `tests/fixtures/agent-publication-contract.json`
is the same file in both repositories; `tests/test_console_desk_agent.py`
fails when this module stops agreeing with it.

Standard library only: `guest_steps.py` imports this module at activation,
which only guarantees python3 and python3-yaml on the target.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

# The contract with nivuus/desk. Change nothing here without changing the
# shared fixture and `hooks/agent_payload.py` in nivuus/desk together.
DESK_AGENT_DIR = "/opt/nivuus/desk/agent"
BINARY_NAME = "agent.exe"
MANIFEST_NAME = "agent.manifest.json"
MANIFEST_SCHEMA = 1
MANIFEST_KEYS = frozenset({"schema", "file", "sha256", "size", "desk_version"})

# Overrides for the test suites, never set in production.
AGENT_DIR_ENV = "NIVUUS_DESK_AGENT_DIR"
# The engine's own variable and default (installer/packages/discovery.py).
PACKAGES_DIR_ENV = "NIVUUS_PACKAGES_DIR"
PACKAGES_DIR_DEFAULT = "/opt/nivuus-packages"
DESK_PACKAGE_MANIFEST = Path("desk") / "nivuus-package.yaml"

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class DeskAgentError(RuntimeError):
    """The published agent cannot be used, and why."""


@dataclass(frozen=True)
class PublishedAgent:
    binary: Path
    manifest: Path
    # The manifest exactly as desk wrote it: what a staged copy is compared
    # with, byte for byte.
    manifest_bytes: bytes
    sha256: str
    size: int
    desk_version: str


def agent_dir() -> Path:
    return Path(os.environ.get(AGENT_DIR_ENV) or DESK_AGENT_DIR)


def desk_installed() -> bool:
    """Is the desk package laid on this machine (its manifest is there)?"""
    packages = os.environ.get(PACKAGES_DIR_ENV) or PACKAGES_DIR_DEFAULT
    return (Path(packages) / DESK_PACKAGE_MANIFEST).is_file()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_manifest(raw: bytes, origin: str) -> dict:
    """The manifest's fields, or DeskAgentError naming what is wrong in it."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DeskAgentError(f"{origin} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DeskAgentError(f"{origin} does not hold a JSON object")
    if set(data) != MANIFEST_KEYS:
        raise DeskAgentError(
            f"{origin} carries the keys {sorted(data)}, expected exactly "
            f"{sorted(MANIFEST_KEYS)}")
    if data["schema"] != MANIFEST_SCHEMA or isinstance(data["schema"], bool):
        raise DeskAgentError(
            f"{origin} is at schema {data['schema']!r}; this console reads "
            f"schema {MANIFEST_SCHEMA}. Update the console package.")
    if data["file"] != BINARY_NAME:
        raise DeskAgentError(
            f"{origin} describes {data['file']!r}, expected {BINARY_NAME!r}")
    if not isinstance(data["sha256"], str) or not _SHA256_RE.fullmatch(data["sha256"]):
        raise DeskAgentError(f"{origin}: 'sha256' is not 64 lowercase hex digits")
    size = data["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise DeskAgentError(f"{origin}: 'size' is not a positive integer")
    if not isinstance(data["desk_version"], str) or not data["desk_version"]:
        raise DeskAgentError(f"{origin}: 'desk_version' is not a non-empty string")
    return data


def published_agent(directory: Path | None = None,
                    installed: bool | None = None) -> PublishedAgent | None:
    """What desk published, None when desk is not on this machine.

    Raises DeskAgentError for every in-between state: see the module
    docstring. The binary's content is NOT read here - `verify()` does that,
    so a caller that only compares manifests stays cheap.
    """
    directory = Path(directory) if directory is not None else agent_dir()
    manifest = directory / MANIFEST_NAME
    binary = directory / BINARY_NAME
    try:
        raw = manifest.read_bytes()
    except FileNotFoundError:
        if binary.exists():
            raise DeskAgentError(
                f"{binary} is there without {manifest}: desk was interrupted "
                "while publishing its agent. Activate the desk package again "
                "(systemctl start nivuus-package-activate@desk, or nivuus "
                "update desk); the binary is not used as it is.") from None
        if desk_installed() if installed is None else installed:
            raise DeskAgentError(
                f"the desk package is installed but has not published its "
                f"agent ({manifest} is absent). Activate desk first "
                "(systemctl start nivuus-package-activate@desk), then run "
                "this again; no other agent.exe is used in its place.") from None
        return None
    except OSError as exc:
        raise DeskAgentError(f"cannot read {manifest}: {exc}") from exc
    data = parse_manifest(raw, str(manifest))
    return PublishedAgent(binary=binary, manifest=manifest, manifest_bytes=raw,
                          sha256=data["sha256"], size=data["size"],
                          desk_version=data["desk_version"])


def verify(published: PublishedAgent, binary: Path | None = None) -> None:
    """Refuse a binary that is not the one the manifest describes.

    `binary` defaults to the published one; pass a staged copy to check that
    a payload still carries what desk published.
    """
    path = Path(binary) if binary is not None else published.binary
    try:
        size = path.stat().st_size
        digest = sha256_of(path) if size == published.size else None
    except OSError as exc:
        raise DeskAgentError(
            f"{published.manifest} describes an agent that cannot be read at "
            f"{path}: {exc}") from exc
    if digest != published.sha256:
        found = (f"sha256 {digest}" if digest is not None
                 else f"{size} bytes instead of {published.size}")
        raise DeskAgentError(
            f"{path} is not the agent desk published: {found}, while "
            f"{published.manifest} says sha256 {published.sha256} "
            f"(desk {published.desk_version}). It is refused. Activate the "
            "desk package again to republish it, or fetch the payload again "
            "if this is a staged copy.")
