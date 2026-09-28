"""A local stand-in for the GitHub releases API, for the updater suites.

It serves `/repos/<owner>/<repo>/releases/latest` and the asset URLs it
advertises, from releases registered in memory. Each test decides exactly
what a release looks like - including the broken shapes the updater must
refuse - without touching the network.
"""
from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import tarfile
import threading


def build_archive(files: dict[str, str | bytes], modes: dict | None = None,
                  extra: list[tarfile.TarInfo] | None = None) -> bytes:
    """A .tar.gz whose members are `files` (relative path -> content)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for rel, content in sorted(files.items()):
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mode = (modes or {}).get(rel, 0o644)
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info)
    return buffer.getvalue()


class FakeGitHub:
    def __init__(self):
        self.releases: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.requests: list[str] = []
        handler = self._handler()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def publish(self, repo: str, version: str, archive: bytes, *,
                sums: str | None = None, digest: str | None = None,
                tag: str | None = None, omit: tuple = (), notes: str = ""):
        """Register `archive` as the latest release of `repo`.

        `sums` and `digest` default to the correct values; tests override
        them to publish a broken release. `omit` drops named assets.
        """
        name = repo.split("/", 1)[1]
        archive_name = f"{name}-{version}.tar.gz"
        good = hashlib.sha256(archive).hexdigest()
        sums = sums if sums is not None else f"{good}  {archive_name}\n"
        digest = digest if digest is not None else f"sha256:{good}"
        base = f"/download/{repo}/v{version}"
        self.blobs[f"{base}/{archive_name}"] = archive
        self.blobs[f"{base}/SHA256SUMS"] = sums.encode()
        assets = [
            {"name": archive_name, "digest": digest,
             "browser_download_url": f"{self.url}{base}/{archive_name}"},
            {"name": "SHA256SUMS",
             "browser_download_url": f"{self.url}{base}/SHA256SUMS"},
        ]
        self.releases[repo] = {
            "tag_name": tag or f"v{version}", "body": notes,
            "assets": [a for a in assets if a["name"] not in omit],
        }

    def _handler(self):
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.requests.append(self.path)
                prefix, suffix = "/repos/", "/releases/latest"
                if self.path.startswith(prefix) and self.path.endswith(suffix):
                    repo = self.path[len(prefix):-len(suffix)]
                    if repo in fake.releases:
                        return self._send(json.dumps(fake.releases[repo]).encode())
                elif self.path in fake.blobs:
                    return self._send(fake.blobs[self.path])
                self.send_response(404)
                self.end_headers()

            def _send(self, body: bytes):
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


def start() -> FakeGitHub:
    """Start a fake API and point the updater's modules at it."""
    fake = FakeGitHub()
    os.environ["NIVUUS_GITHUB_API"] = fake.url
    return fake
