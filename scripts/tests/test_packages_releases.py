#!/usr/bin/env python3
"""Tests for installer/packages/releases.py - finding and verifying releases.

Verification is fail-closed: every case below where something is missing or
disagrees must end in ReleaseError with nothing written to the cache.

Run: python3 scripts/tests/test_packages_releases.py
"""
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "installer"))
sys.path.insert(0, str(HERE))

import fake_github  # noqa: E402

fake = fake_github.start()

from packages import releases  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_refused(label, fn, needle, cache=None):
    try:
        fn()
    except releases.ReleaseError as exc:
        if needle not in str(exc):
            failures.append(f"{label}: message {str(exc)!r} lacks {needle!r}")
        if cache is not None and os.path.isdir(cache) and os.listdir(cache):
            failures.append(f"{label}: cache not empty: {os.listdir(cache)}")
        return
    failures.append(f"{label}: expected ReleaseError, none raised")


archive = fake_github.build_archive({"nivuus-package.yaml": "name: x\n"})
REPO_NAME = "nivuus/home-stock"

try:
    fake.publish(REPO_NAME, "1.2.0", archive, notes="## Changes")
    release = releases.latest_release(REPO_NAME)
    check("version from tag", release.version, "1.2.0")
    check("archive name", release.archive_name, "home-stock-1.2.0.tar.gz")
    check("notes kept", release.notes, "## Changes")
    with tempfile.TemporaryDirectory() as cache:
        path = releases.download(release, cache)
        check("download lands in the cache",
              path, os.path.join(cache, "home-stock-1.2.0.tar.gz"))
        with open(path, "rb") as fh:
            check("downloaded bytes are the archive", fh.read(), archive)

    check("versions sort numerically, not as text",
          releases.version_key("1.10.0") > releases.version_key("1.9.3"), True)

    fake.publish(REPO_NAME, "1.2.0", archive, tag="latest")
    check_refused("non-semver tag", lambda: releases.latest_release(REPO_NAME),
                  "not vMAJOR.MINOR.PATCH")

    for omitted in ("home-stock-1.2.0.tar.gz", "SHA256SUMS"):
        fake.publish(REPO_NAME, "1.2.0", archive, omit=(omitted,))
        check_refused(f"missing {omitted}",
                      lambda: releases.latest_release(REPO_NAME), omitted)

    check_refused("unknown repository",
                  lambda: releases.latest_release("nivuus/nothing"), "404")

    with tempfile.TemporaryDirectory() as root:
        cache = os.path.join(root, "cache")
        wrong = "0" * 64
        fake.publish(REPO_NAME, "1.2.0", archive,
                     sums=f"{wrong}  home-stock-1.2.0.tar.gz\n",
                     digest=f"sha256:{wrong}")
        release = releases.latest_release(REPO_NAME)
        check_refused("wrong sum", lambda: releases.download(release, cache),
                      "SHA256SUMS says", cache)

        fake.publish(REPO_NAME, "1.2.0", archive, digest="sha256:" + "1" * 64)
        release = releases.latest_release(REPO_NAME)
        check_refused("API digest disagrees with SHA256SUMS",
                      lambda: releases.download(release, cache),
                      "disagree", cache)

        fake.publish(REPO_NAME, "1.2.0", archive,
                     sums=f"{wrong}  other-1.0.0.tar.gz\n")
        release = releases.latest_release(REPO_NAME)
        check_refused("archive absent from SHA256SUMS",
                      lambda: releases.download(release, cache),
                      "does not list", cache)

        # A release published before GitHub exposed digests still verifies,
        # on SHA256SUMS alone.
        fake.publish(REPO_NAME, "1.2.0", archive, digest="")
        release = releases.latest_release(REPO_NAME)
        check("no API digest still verifies on SHA256SUMS",
              os.path.basename(releases.download(release, cache)),
              "home-stock-1.2.0.tar.gz")
finally:
    fake.close()


if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all release tests passed")
