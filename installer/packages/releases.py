"""Finding a package's latest GitHub release, and fetching it verified.

Every Nivuus repository publishes the same three assets through the shared
release workflow: `<name>-<version>.tar.gz` (a `git archive HEAD`, with the
real version injected into the manifest), `SHA256SUMS`, and the notes as the
release body. The tag `vMAJOR.MINOR.PATCH` is the only source of truth for
the version.

Verification is FAIL-CLOSED. The archive's sum must be found in SHA256SUMS
and match what was downloaded; when the API also exposes the asset's digest
(GitHub computes one at upload time), the two must agree. Any impossibility -
a missing asset, an unreachable URL, an archive absent from SHA256SUMS - is an
error, never a reason to skip the check: a network fault must not become a
way around it.

Anonymous API calls only: all the suite's repositories are public, and the
daily check makes one request per installed package against a limit of 60
per hour.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

GITHUB_API = os.environ.get("NIVUUS_GITHUB_API", "https://api.github.com")
TIMEOUT = 30
USER_AGENT = "nivuus-updater"
SUMS_ASSET = "SHA256SUMS"
TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")


class ReleaseError(RuntimeError):
    """Raised when a release cannot be found, fetched or verified."""


@dataclass(frozen=True)
class Release:
    repo: str
    tag: str
    version: str
    archive_name: str
    archive_url: str
    sums_url: str
    # "" when the API exposes no digest for the archive asset.
    archive_digest: str
    notes: str


def version_key(version: str) -> tuple[int, int, int]:
    """A sortable key for MAJOR.MINOR.PATCH."""
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


def _get(url: str, accept: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"Accept": accept, "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise ReleaseError(f"{url}: {exc}") from exc


def latest_release(repo: str) -> Release:
    """The latest published release of `repo` (owner/name)."""
    url = f"{GITHUB_API}/repos/{repo}/releases/latest"
    try:
        data = json.loads(_get(url, "application/vnd.github+json"))
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"{url}: not JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ReleaseError(f"{url}: unexpected response")

    tag = str(data.get("tag_name") or "")
    match = TAG_RE.match(tag)
    if not match:
        raise ReleaseError(
            f"{repo}: latest release tag {tag!r} is not vMAJOR.MINOR.PATCH")
    version = match.group(1)

    assets = {a.get("name"): a for a in data.get("assets") or []
              if isinstance(a, dict)}
    name = repo.split("/", 1)[1]
    archive_name = f"{name}-{version}.tar.gz"
    for wanted in (archive_name, SUMS_ASSET):
        if wanted not in assets:
            raise ReleaseError(
                f"{repo} {tag}: asset {wanted!r} is missing from the release")

    digest = str(assets[archive_name].get("digest") or "")
    if digest and not digest.startswith("sha256:"):
        raise ReleaseError(
            f"{repo} {tag}: unsupported digest {digest!r} for {archive_name}")
    return Release(
        repo=repo, tag=tag, version=version, archive_name=archive_name,
        archive_url=str(assets[archive_name]["browser_download_url"]),
        sums_url=str(assets[SUMS_ASSET]["browser_download_url"]),
        archive_digest=digest.removeprefix("sha256:"),
        notes=str(data.get("body") or ""))


def _expected_sum(sums: str, archive_name: str) -> str:
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == archive_name:
            return parts[0].lower()
    raise ReleaseError(f"{SUMS_ASSET} does not list {archive_name}")


def download(release: Release, cache_dir: str) -> str:
    """Download the release archive into `cache_dir` and verify it.

    Returns the archive's path. The archive is verified in memory and only
    written once it matches, so nothing unverified ever lands in the cache
    where a later run could mistake it for a good download.
    """
    sums = _get(release.sums_url, "application/octet-stream").decode(
        "utf-8", errors="replace")
    expected = _expected_sum(sums, release.archive_name)
    if release.archive_digest and release.archive_digest.lower() != expected:
        raise ReleaseError(
            f"{release.repo} {release.tag}: {SUMS_ASSET} and the GitHub "
            f"asset digest disagree for {release.archive_name}")

    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, release.archive_name)
    payload = _get(release.archive_url, "application/octet-stream")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise ReleaseError(
            f"{release.repo} {release.tag}: {release.archive_name} has "
            f"sha256 {actual}, {SUMS_ASSET} says {expected}")
    with open(path, "wb") as fh:
        fh.write(payload)
    return path
