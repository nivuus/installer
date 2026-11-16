#!/usr/bin/env python3
"""The contract with nivuus/desk on agent.exe, and the reader that enforces it.

desk is the single owner of agent.exe: it publishes the binary and a manifest
under /opt/nivuus/desk/agent, and this package consumes them from there.
`fixtures/agent-publication-contract.json` is the SAME file in both
repositories (nivuus/desk: tests/fixtures/), pinned here by its sha256: a
change of path or of manifest format has to change that file, hence the pin,
hence the other repository's copy and pin.

Run: python3 console/tests/test_console_desk_agent.py
"""
import hashlib
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import desk_agent  # noqa: E402
import desk_agent_fixture  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def refused(label, call, *needles):
    try:
        call()
    except desk_agent.DeskAgentError as exc:
        for needle in needles:
            check(f"{label}: the refusal says {needle!r}", needle in str(exc), True)
        return
    failures.append(f"{label}: accepted")


# --- the shared fixture ---------------------------------------------------
FIXTURE = HERE / "fixtures" / "agent-publication-contract.json"
# Identical in nivuus/desk's tests/test_desk_agent_contract.py. Editing the
# fixture means editing this pin in BOTH repositories.
FIXTURE_SHA256 = "ec159dca35360713b885bac96ecbc4d075bfbc5dcfbc368432fa55fac90a438f"
raw = FIXTURE.read_bytes()
check("the contract fixture is the one both repositories pinned",
      hashlib.sha256(raw).hexdigest(), FIXTURE_SHA256)
contract = json.loads(raw)
example = contract["example"]
example_binary = bytes.fromhex(example["binary_hex"])
example_manifest = example["manifest_text"].encode("utf-8")

check("contract version", contract["contract"], desk_agent.MANIFEST_SCHEMA)
check("the publication directory", desk_agent.DESK_AGENT_DIR, contract["directory"])
check("the binary's name", desk_agent.BINARY_NAME, contract["binary"])
check("the manifest's name", desk_agent.MANIFEST_NAME, contract["manifest"])

# The reader accepts the example exactly as desk writes it.
with tempfile.TemporaryDirectory() as tmp:
    directory = pathlib.Path(tmp) / "agent"
    directory.mkdir()
    (directory / contract["binary"]).write_bytes(example_binary)
    (directory / contract["manifest"]).write_bytes(example_manifest)
    agent = desk_agent.published_agent(directory, installed=True)
    check("the example manifest is read", agent is not None, True)
    check("its sha256", agent.sha256, hashlib.sha256(example_binary).hexdigest())
    check("its size", agent.size, len(example_binary))
    check("its desk version", agent.desk_version, example["desk_version"])
    check("the manifest bytes are kept verbatim", agent.manifest_bytes,
          example_manifest)
    desk_agent.verify(agent)       # raises on a mismatch

# The suites' own publisher writes that same format (it stands in for desk
# in every other console suite).
check("the test publisher writes the contract's manifest, byte for byte",
      desk_agent_fixture.manifest_text(example_binary, example["desk_version"]),
      example["manifest_text"])

# --- the three states, and nothing in between ------------------------------
with tempfile.TemporaryDirectory() as tmp:
    directory = pathlib.Path(tmp) / "agent"
    check("nothing published, desk not installed: no agent, no error",
          desk_agent.published_agent(directory, installed=False), None)
    refused("desk installed, nothing published",
            lambda: desk_agent.published_agent(directory, installed=True),
            "has not published", "nivuus-package-activate@desk")
    directory.mkdir()
    (directory / desk_agent.BINARY_NAME).write_bytes(b"MZ orphan")
    refused("a binary without its manifest, desk not installed",
            lambda: desk_agent.published_agent(directory, installed=False),
            "interrupted", "not used")

# desk_installed() reads the engine's packages directory.
agent_dir = desk_agent_fixture.isolate()
check("isolated: desk is not installed", desk_agent.desk_installed(), False)
check("isolated: nothing is published", desk_agent.published_agent(), None)
desk_agent_fixture.install_desk_package()
check("with desk's manifest laid: desk is installed",
      desk_agent.desk_installed(), True)
refused("installed and unpublished, through the environment",
        desk_agent.published_agent, str(agent_dir))
desk_agent_fixture.publish(agent_dir)
check("once published, it is found through the environment",
      desk_agent.published_agent().binary, agent_dir / desk_agent.BINARY_NAME)

# --- a binary that is not the published one is refused ----------------------
with tempfile.TemporaryDirectory() as tmp:
    directory = pathlib.Path(tmp) / "agent"
    desk_agent_fixture.publish(directory, b"MZ the published build")
    agent = desk_agent.published_agent(directory)
    binary = directory / desk_agent.BINARY_NAME
    binary.write_bytes(b"MZ the PUBLISHED build")       # same size
    refused("same size, other content", lambda: desk_agent.verify(agent),
            "is not the agent desk published", agent.sha256)
    binary.write_bytes(b"MZ trunc")
    refused("truncated", lambda: desk_agent.verify(agent), "bytes instead of")
    binary.unlink()
    refused("manifest without a binary", lambda: desk_agent.verify(agent),
            "cannot be read")
    other = pathlib.Path(tmp) / "copy.exe"
    other.write_bytes(b"MZ the published build")
    desk_agent.verify(agent, other)     # an identical copy elsewhere passes

# --- a manifest that drifted from the format is refused, never guessed ------
good = json.loads(example_manifest)


def manifest_with(**changes):
    data = dict(good, **changes)
    return json.dumps({k: v for k, v in data.items() if v is not ...}).encode()


for label, blob, needle in [
    ("not JSON", b"{", "not valid JSON"),
    ("not an object", b"[]", "JSON object"),
    ("a missing key", manifest_with(size=...), "expected exactly"),
    ("an extra key", manifest_with(built_at="today"), "expected exactly"),
    ("another schema", manifest_with(schema=2), "schema"),
    ("a boolean schema", manifest_with(schema=True), "schema"),
    ("another file name", manifest_with(file="agent2.exe"), "agent2.exe"),
    ("an upper-case digest", manifest_with(sha256=good["sha256"].upper()), "sha256"),
    ("a short digest", manifest_with(sha256="abc"), "sha256"),
    ("a zero size", manifest_with(size=0), "size"),
    ("a string size", manifest_with(size="34"), "size"),
    ("an empty version", manifest_with(desk_version=""), "desk_version"),
]:
    refused(label, lambda blob=blob: desk_agent.parse_manifest(blob, "the manifest"),
            needle)

if failures:
    print(f"FAIL ({len(failures)})")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print("OK - desk agent contract checks passed")
