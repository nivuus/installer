"""Test support: a desk agent publication under a temporary directory.

`publish()` writes agent.exe and its manifest the way nivuus/desk does
(`hooks/agent_payload.py` there), so the consumer suites exercise the real
reader against real files. That this recipe IS desk's is not assumed here:
test_console_desk_agent.py compares its output, byte for byte, with the
example of the fixture shared by both repositories.

`isolate()` points the two environment overrides at paths that do not exist,
so a suite never concludes from whatever desk happens to be installed on the
machine running it. Call it before importing anything that plans steps.
"""
import hashlib
import json
import os
import pathlib
import tempfile

import desk_agent


def manifest_text(content: bytes, desk_version: str) -> str:
    return json.dumps({"schema": desk_agent.MANIFEST_SCHEMA,
                       "file": desk_agent.BINARY_NAME,
                       "sha256": hashlib.sha256(content).hexdigest(),
                       "size": len(content),
                       "desk_version": desk_version},
                      indent=2, sort_keys=True) + "\n"


def publish(directory: pathlib.Path, content: bytes = b"MZ fake agent\n",
            desk_version: str = "1.2.5") -> bytes:
    """Publish `content` as the agent under `directory`; return the manifest."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / desk_agent.BINARY_NAME).write_bytes(content)
    manifest = manifest_text(content, desk_version).encode("utf-8")
    (directory / desk_agent.MANIFEST_NAME).write_bytes(manifest)
    return manifest


def isolate() -> pathlib.Path:
    """No desk on this machine, as far as the suites can tell.

    Returns the (absent) publication directory, so a suite can publish there.
    """
    base = pathlib.Path(tempfile.mkdtemp(prefix="nivuus-no-desk-"))
    os.environ[desk_agent.AGENT_DIR_ENV] = str(base / "desk-agent")
    os.environ[desk_agent.PACKAGES_DIR_ENV] = str(base / "packages")
    return base / "desk-agent"


def install_desk_package() -> None:
    """Make desk look installed (its manifest laid under the packages dir)."""
    manifest = (pathlib.Path(os.environ[desk_agent.PACKAGES_DIR_ENV])
                / desk_agent.DESK_PACKAGE_MANIFEST)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("name: desk\n")
