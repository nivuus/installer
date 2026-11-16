"""Ship /etc/docker/daemon.json: bounded container logs and build-cache GC.

Without it Docker falls back to `json-file` with no rotation, and every
container logs without limit under /var/lib/docker/containers - the way the
root disk of the reference host filled on 2026-11-03. The values are the ones
docs/claude/host-ops-audits.md records for that host (50m x 3 per container,
builder GC ceiling 20 GB).

The file is MERGED, never clobbered: whatever an existing daemon.json already
sets wins, the template only fills what is missing. The result is validated as
JSON and moved into place atomically, so dockerd never reads a half-written
file. An existing file that is not a JSON object is left untouched and
reported - overwriting an operator's file we cannot read is worse than not
shipping ours.

Deliberately NOT in the template: `"iptables": false`. It is what keeps
published ports behind firewalld on the reference host, but it also removes
Docker's own NAT, so it is only safe where the host firewall provides
masquerading for the Docker bridges. That is an owner decision (audit 3.7).
"""
from __future__ import annotations

import json
import os
import tempfile

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
DAEMON_JSON = "etc/docker/daemon.json"
TEMPLATE = os.path.join(TEMPLATES_DIR, "docker-daemon.json")


class DaemonConfigError(ValueError):
    """The existing daemon.json cannot be merged safely."""


def load_template(path: str = TEMPLATE) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise DaemonConfigError(f"{path} must contain a JSON object")
    return data


def merge_daemon_config(existing: dict, template: dict) -> dict:
    """Fill what `existing` lacks from `template`; never override a value.

    Nested objects are merged key by key. `log-opts` belongs to a log driver:
    when the existing file picks another driver, the template's options are
    for the wrong driver and are not added.
    """
    merged = dict(existing)
    for key, value in template.items():
        if key == "log-opts" and existing.get("log-driver", template.get(
                "log-driver")) != template.get("log-driver"):
            continue
        if key not in merged:
            merged[key] = value
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = merge_daemon_config(merged[key], value)
    return merged


def render_daemon_config(existing_text: str | None, template: dict) -> str:
    """Return the JSON text to install. Raises DaemonConfigError if the
    existing text is not a JSON object."""
    existing: dict = {}
    if existing_text is not None and existing_text.strip():
        try:
            existing = json.loads(existing_text)
        except json.JSONDecodeError as exc:
            raise DaemonConfigError(f"existing daemon.json is not valid JSON: {exc}") from exc
        if not isinstance(existing, dict):
            raise DaemonConfigError("existing daemon.json is not a JSON object")
    text = json.dumps(merge_daemon_config(existing, template), indent=2) + "\n"
    json.loads(text)  # validate what is about to be installed
    return text


def _atomic_write(path: str, text: str, mode: int = 0o644) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".daemon.json.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def install_daemon_config(target: str, emit, template_path: str = TEMPLATE) -> bool:
    """Install the merged daemon.json under `target`. Returns True if written."""
    path = os.path.join(target, DAEMON_JSON)
    try:
        with open(path, encoding="utf-8") as fh:
            existing_text = fh.read()
    except FileNotFoundError:
        existing_text = None
    try:
        text = render_daemon_config(existing_text, load_template(template_path))
    except DaemonConfigError as exc:
        emit.warn("features", 92, f"Docker daemon.json left untouched: {exc}")
        return False
    if text == existing_text:
        return False
    _atomic_write(path, text)
    emit.info("features", 92, "Docker daemon.json installed (log rotation, builder GC)")
    return True
