# Ambilight Air — Protocol Reverse Engineering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a tested, Home-Assistant-free Python library for the Philips Ambilight Air wire protocol, plus the tooling to run the experiments that resolve the protocol's remaining unknowns, and publish the resulting specification.

**Architecture:** A pure codec (`frame.py`, no I/O) sits under an asyncio multicast transport (`transport.py`). CLI tools in `tools/` compose the two to sniff, inject, and probe the real speakers. The 909-frame reference capture taken on 2026-08-01 is the permanent test fixture: any misreading of the wire format fails the round-trip test.

**Tech Stack:** Python 3.13, stdlib only at runtime (`socket`, `struct`, `asyncio`), pytest + ruff + mypy for development, GitHub Actions for CI.

**Spec:** `docs/superpowers/specs/2026-08-01-ambilight-air-reverse-engineering-design.md` (in the Nivuus repo).

**Scope note:** This plan covers the RE phase only — the codec, the tooling, the experiments, and the published protocol spec. The Home Assistant integration is deliberately a *second* plan, written after these experiments land, because the entity model (spec §4.4) and the colour conversion table (spec §5.2) are outputs of the experiments here. Planning integration code now would mean inventing it.

## Global Constraints

- Python `>=3.13` — matches the Home Assistant runtime the integration will later target.
- The protocol package has **zero runtime dependencies** outside the standard library, and **never imports `homeassistant`**. This is the spec's structuring rule (§4.2) and is enforced by a test.
- Licence **Apache-2.0** (the Home Assistant core licence, so no relicensing is needed at submission time).
- All code comments, docstrings, identifiers and committed documentation in **English**. The published protocol spec is English.
- Maximum **200 lines per source file**; split by responsibility when exceeded.
- Repository: `~/Projects/ha-ambilight-air`, GitHub remote `mallanic/ha-ambilight-air`.
- Reference capture available at `/home/mallanic/Projects/.ambilight-air-captures/al-air-reference-20260801.pcap` (909 frames, 12 s, taken 2026-08-01).
- Known network facts, used verbatim by the tools: multicast group `224.8.0.8`, UDP port `2920`, host interface `192.168.0.1` (`localBridge`), speaker A `192.168.0.68`, speaker B `192.168.0.214`, TV/transmitter `192.168.0.183` with sender field `788A866B7C5F0239`.
- **All experiments are run with the television powered off**, so the two emitters never compete (spec §3, §5.3).
- Experiments write **only rendering frames** to the speakers — never firmware, never configuration.

---

### Task 1: Repository bootstrap and frame decoder

**Files:**
- Create: `~/Projects/ha-ambilight-air/pyproject.toml`
- Create: `~/Projects/ha-ambilight-air/.gitignore`
- Create: `~/Projects/ha-ambilight-air/LICENSE`
- Create: `~/Projects/ha-ambilight-air/src/ambilight_air_protocol/__init__.py`
- Create: `~/Projects/ha-ambilight-air/src/ambilight_air_protocol/frame.py`
- Test: `~/Projects/ha-ambilight-air/tests/test_frame.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `MAGIC: bytes`, `VERSION: bytes`, `FRAME_SIZE: int = 120`, `ZONE_COUNT: int = 10`, `class FrameError(ValueError)`, `class Zone(red: int, green: int, blue: int)` (frozen dataclass), `class Frame(sender: str, zones: tuple[Zone, ...], version: bytes)` (frozen dataclass), `decode(buf: bytes) -> Frame`.

- [ ] **Step 1: Create the repository skeleton**

```bash
mkdir -p ~/Projects/ha-ambilight-air/{src/ambilight_air_protocol,tools,tests/fixtures,docs/experiments}
cd ~/Projects/ha-ambilight-air
git init -b main
curl -sL https://www.apache.org/licenses/LICENSE-2.0.txt -o LICENSE
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q pytest ruff mypy
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "ambilight-air-protocol"
version = "0.1.0"
description = "Wire protocol for Philips Ambilight Air"
requires-python = ">=3.13"
license = "Apache-2.0"
dependencies = []

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]

[tool.mypy]
python_version = "3.13"
strict = true
mypy_path = "src"
```

- [ ] **Step 3: Write `.gitignore`**

```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
```

- [ ] **Step 4: Install the reference capture as a fixture**

```bash
cp /home/mallanic/Projects/.ambilight-air-captures/al-air-reference-20260801.pcap \
   ~/Projects/ha-ambilight-air/tests/fixtures/
```

- [ ] **Step 5: Write the failing test**

Create `tests/test_frame.py`:

```python
"""Tests for the Ambilight Air wire format."""

import pytest

from ambilight_air_protocol.frame import (
    FRAME_SIZE,
    MAGIC,
    ZONE_COUNT,
    FrameError,
    decode,
)

# First datagram of the 2026-08-01 reference capture, payload only.
# Verified byte-identical to the capture; laid out one record per line.
REFERENCE_FRAME = bytes.fromhex(
    "416d62696c69676874416972"          # b"AmbilightAir"
    "0100"                              # version
    "37383841383636423743354630323339"  # b"788A866B7C5F0239"
    "000001" "0100" "0100" "0100"       # zone 1
    "000002" "0253" "0201" "016f"       # zone 2
    "000003" "02df" "0265" "01ac"       # zone 3
    "000004" "0100" "0100" "0100"       # zone 4
    "000005" "020a" "018a" "0127"       # zone 5
    "000006" "017c" "0134" "00fa"       # zone 6
    "000007" "0506" "0413" "0357"       # zone 7
    "000008" "1b97" "0e42" "0800"       # zone 8
    "000009" "02ee" "0219" "0163"       # zone 9
    "00000a" "010d" "00cd" "00a1"       # zone 10
)


def test_reference_frame_is_120_bytes():
    assert len(REFERENCE_FRAME) == FRAME_SIZE


def test_decode_reference_frame():
    frame = decode(REFERENCE_FRAME)
    assert frame.sender == "788A866B7C5F0239"
    assert frame.version == b"\x01\x00"
    assert len(frame.zones) == ZONE_COUNT
    assert frame.zones[0] == (256, 256, 256)
    assert frame.zones[1] == (595, 513, 367)
    assert frame.zones[7] == (7063, 3650, 2048)
    assert frame.zones[9] == (269, 205, 161)


def test_decode_rejects_wrong_size():
    with pytest.raises(FrameError, match="expected 120 bytes"):
        decode(REFERENCE_FRAME[:-1])


def test_decode_rejects_bad_magic():
    corrupted = b"X" + REFERENCE_FRAME[1:]
    with pytest.raises(FrameError, match="magic"):
        decode(corrupted)


def test_decode_rejects_nonzero_padding():
    corrupted = bytearray(REFERENCE_FRAME)
    corrupted[30] = 0xFF  # padding byte of record 0
    with pytest.raises(FrameError, match="padding"):
        decode(bytes(corrupted))


def test_decode_rejects_out_of_order_zone_index():
    corrupted = bytearray(REFERENCE_FRAME)
    corrupted[32] = 0x07  # index byte of record 0, should be 1
    with pytest.raises(FrameError, match="index"):
        decode(bytes(corrupted))


def test_magic_is_ascii_marker():
    assert MAGIC == b"AmbilightAir"
```

> The hex literal above is split across lines purely for readability; Python
> concatenates it into one string. If a byte is mistyped the first test fails
> immediately on the length check, so the error surfaces at once.

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd ~/Projects/ha-ambilight-air && .venv/bin/pytest tests/test_frame.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ambilight_air_protocol.frame'`

- [ ] **Step 7: Write the decoder**

Create `src/ambilight_air_protocol/__init__.py` (empty file) and `src/ambilight_air_protocol/frame.py`:

```python
"""Ambilight Air wire format: pure encode/decode, no I/O, no framework.

Layout of a 120-byte datagram, as observed on the wire and validated against
909 frames of the 2026-08-01 reference capture:

    offset  size  content
         0    12  b"AmbilightAir"
        12     2  version, always b"\\x01\\x00" so far
        14    16  sender identity, ASCII (transmitter MAC + 4-char suffix)
        30    90  10 records of 9 bytes

Each record is::

    00 00 <index>  <red:u16 BE>  <green:u16 BE>  <blue:u16 BE>

with ``index`` running 1..10 in order.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import NamedTuple

MAGIC = b"AmbilightAir"
VERSION = b"\x01\x00"
FRAME_SIZE = 120
ZONE_COUNT = 10

_HEADER_SIZE = 30
_SENDER_SIZE = 16
_RECORD_SIZE = 9
_PADDING = b"\x00\x00"


class FrameError(ValueError):
    """Raised when a buffer is not a well-formed Ambilight Air frame."""


class Zone(NamedTuple):
    """One lighting zone. Units are unresolved; see docs/protocol.md."""

    red: int
    green: int
    blue: int


@dataclass(frozen=True, slots=True)
class Frame:
    """A decoded Ambilight Air datagram."""

    sender: str
    zones: tuple[Zone, ...]
    version: bytes = VERSION


def decode(buf: bytes) -> Frame:
    """Decode a datagram payload, raising FrameError on any deviation."""
    if len(buf) != FRAME_SIZE:
        raise FrameError(f"expected {FRAME_SIZE} bytes, got {len(buf)}")
    if buf[0:12] != MAGIC:
        raise FrameError(f"bad magic: {buf[0:12]!r}")

    sender = buf[14:30].decode("ascii", errors="replace")
    zones: list[Zone] = []
    for n in range(ZONE_COUNT):
        base = _HEADER_SIZE + n * _RECORD_SIZE
        if buf[base : base + 2] != _PADDING:
            raise FrameError(
                f"record {n}: expected zero padding, got {buf[base : base + 2].hex()}"
            )
        index = buf[base + 2]
        if index != n + 1:
            raise FrameError(f"record {n}: expected index {n + 1}, got {index}")
        zones.append(Zone(*struct.unpack_from(">HHH", buf, base + 3)))

    return Frame(sender=sender, zones=tuple(zones), version=buf[12:14])
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_frame.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 9: Commit**

```bash
cd ~/Projects/ha-ambilight-air
git add -A
git commit -m "feat(frame): decode Ambilight Air datagrams

Layout validated against the first frame of the 2026-08-01 reference
capture. Decoder is strict: wrong size, bad magic, non-zero record
padding and out-of-order zone indices all raise FrameError, so a
misreading of the wire format cannot pass silently."
```

---

### Task 2: Frame encoder and round-trip against the full capture

This is the task that proves the wire format is genuinely understood rather than plausibly guessed: every one of the 909 captured frames must survive `decode` → `encode` byte-identically.

**Files:**
- Create: `~/Projects/ha-ambilight-air/tools/pcap_to_payloads.py`
- Create: `~/Projects/ha-ambilight-air/tests/fixtures/al-air-reference-20260801.bin`
- Modify: `~/Projects/ha-ambilight-air/src/ambilight_air_protocol/frame.py`
- Modify: `~/Projects/ha-ambilight-air/tests/test_frame.py`

**Interfaces:**
- Consumes: `decode`, `Frame`, `Zone`, `FrameError`, `MAGIC`, `FRAME_SIZE` from Task 1.
- Produces: `encode(frame: Frame) -> bytes`; the fixture file `tests/fixtures/al-air-reference-20260801.bin` holding 909 concatenated 120-byte payloads.

- [ ] **Step 1: Write the payload extractor**

Create `tools/pcap_to_payloads.py`. It locates payloads by magic rather than parsing link layers, which keeps it dependency-free and immune to capture-format differences:

```python
"""Extract Ambilight Air payloads from a pcap into a flat binary fixture.

Usage:
    python tools/pcap_to_payloads.py capture.pcap out.bin

Payloads are located by scanning for the magic marker rather than parsing
the pcap and link-layer headers, which keeps this tool dependency-free.
"""

from __future__ import annotations

import sys
from pathlib import Path

MAGIC = b"AmbilightAir"
FRAME_SIZE = 120


def extract(data: bytes) -> list[bytes]:
    payloads: list[bytes] = []
    offset = 0
    while (index := data.find(MAGIC, offset)) >= 0:
        payload = data[index : index + FRAME_SIZE]
        if len(payload) == FRAME_SIZE:
            payloads.append(payload)
        offset = index + 1
    return payloads


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    payloads = extract(source.read_bytes())
    target.write_bytes(b"".join(payloads))
    print(f"extracted {len(payloads)} payloads -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Generate the fixture**

```bash
cd ~/Projects/ha-ambilight-air
.venv/bin/python tools/pcap_to_payloads.py \
  tests/fixtures/al-air-reference-20260801.pcap \
  tests/fixtures/al-air-reference-20260801.bin
```

Expected output: `extracted 909 payloads -> tests/fixtures/al-air-reference-20260801.bin`
Verify: `ls -l tests/fixtures/*.bin` shows exactly `909 * 120 = 109080` bytes.

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_frame.py`:

```python
from pathlib import Path

from ambilight_air_protocol.frame import Frame, Zone, encode

FIXTURE = Path(__file__).parent / "fixtures" / "al-air-reference-20260801.bin"


def reference_payloads() -> list[bytes]:
    data = FIXTURE.read_bytes()
    assert len(data) % FRAME_SIZE == 0
    return [data[i : i + FRAME_SIZE] for i in range(0, len(data), FRAME_SIZE)]


def test_fixture_holds_the_full_capture():
    assert len(reference_payloads()) == 909


def test_every_captured_frame_round_trips_byte_identically():
    """The decisive test: a misread field cannot survive re-encoding."""
    for position, payload in enumerate(reference_payloads()):
        assert encode(decode(payload)) == payload, f"mismatch at frame {position}"


def test_capture_invariants_hold_across_all_frames():
    frames = [decode(p) for p in reference_payloads()]
    assert {f.version for f in frames} == {b"\x01\x00"}
    assert {f.sender for f in frames} == {"788A866B7C5F0239"}
    assert all(len(f.zones) == ZONE_COUNT for f in frames)


def test_encode_rejects_wrong_zone_count():
    with pytest.raises(FrameError, match="10 zones"):
        encode(Frame(sender="788A866B7C5F0239", zones=(Zone(0, 0, 0),)))


def test_encode_rejects_bad_sender_length():
    zones = tuple(Zone(0, 0, 0) for _ in range(ZONE_COUNT))
    with pytest.raises(FrameError, match="16 ASCII"):
        encode(Frame(sender="TOO_SHORT", zones=zones))


def test_encode_rejects_out_of_range_component():
    zones = tuple(Zone(0x1FFFF, 0, 0) for _ in range(ZONE_COUNT))
    with pytest.raises(FrameError, match="range"):
        encode(Frame(sender="788A866B7C5F0239", zones=zones))
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_frame.py -v`
Expected: FAIL — `ImportError: cannot import name 'encode'`

- [ ] **Step 5: Write the encoder**

Append to `src/ambilight_air_protocol/frame.py`:

```python
_MAX_COMPONENT = 0xFFFF


def encode(frame: Frame) -> bytes:
    """Serialise a frame to its 120-byte wire representation."""
    if len(frame.zones) != ZONE_COUNT:
        raise FrameError(f"expected {ZONE_COUNT} zones, got {len(frame.zones)}")

    sender = frame.sender.encode("ascii")
    if len(sender) != _SENDER_SIZE:
        raise FrameError(f"sender must be {_SENDER_SIZE} ASCII characters")

    out = bytearray(MAGIC + frame.version + sender)
    for index, zone in enumerate(frame.zones, start=1):
        for component in zone:
            if not 0 <= component <= _MAX_COMPONENT:
                raise FrameError(f"component {component} out of range 0..{_MAX_COMPONENT}")
        out += _PADDING + bytes([index]) + struct.pack(">HHH", *zone)

    if len(out) != FRAME_SIZE:  # pragma: no cover - structural guard
        raise FrameError(f"encoded {len(out)} bytes, expected {FRAME_SIZE}")
    return bytes(out)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_frame.py -v`
Expected: PASS. `test_every_captured_frame_round_trips_byte_identically` passing over 909 frames is the acceptance criterion for the wire format.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(frame): encode frames, round-trip verified on 909 captured frames

Every frame of the reference capture survives decode -> encode byte for
byte, which rules out a misread field being silently tolerated."
```

---

### Task 3: Multicast transport

**Files:**
- Create: `~/Projects/ha-ambilight-air/src/ambilight_air_protocol/transport.py`
- Test: `~/Projects/ha-ambilight-air/tests/test_transport.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (transport is codec-agnostic; it moves bytes).
- Produces: `GROUP: str = "224.8.0.8"`, `PORT: int = 2920`, `create_rx_socket(interface_ip: str) -> socket.socket`, `create_tx_socket(interface_ip: str, ttl: int = 1, loopback: bool = False) -> socket.socket`, `send(sock: socket.socket, payload: bytes) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_transport.py`. The test uses multicast loopback on the local host, so it needs no speakers and runs in CI:

```python
"""Transport tests. These use multicast loopback and need no hardware."""

import socket

import pytest

from ambilight_air_protocol.transport import (
    GROUP,
    PORT,
    create_rx_socket,
    create_tx_socket,
    send,
)

LOOPBACK = "127.0.0.1"


def test_group_and_port_match_the_observed_protocol():
    assert GROUP == "224.8.0.8"
    assert PORT == 2920


def test_datagram_sent_to_the_group_is_received():
    rx = create_rx_socket(LOOPBACK)
    tx = create_tx_socket(LOOPBACK, loopback=True)
    try:
        rx.settimeout(2.0)
        payload = b"round-trip probe"
        send(tx, payload)
        received, _address = rx.recvfrom(2048)
        assert received == payload
    finally:
        rx.close()
        tx.close()


def test_rx_socket_is_non_blocking_by_default():
    rx = create_rx_socket(LOOPBACK)
    try:
        assert rx.gettimeout() == 0.0
    finally:
        rx.close()


def test_two_receivers_can_share_the_port():
    """Address reuse matters: the sniffer and the integration coexist."""
    first = create_rx_socket(LOOPBACK)
    try:
        second = create_rx_socket(LOOPBACK)
        second.close()
    except OSError as error:  # pragma: no cover - regression guard
        pytest.fail(f"port sharing rejected: {error}")
    finally:
        first.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_transport.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ambilight_air_protocol.transport'`

- [ ] **Step 3: Write the transport**

Create `src/ambilight_air_protocol/transport.py`:

```python
"""UDP multicast transport for Ambilight Air.

The mDNS record advertises ``_tpv_al._tcp`` on port 2920, but the port is
closed over TCP and open over UDP -- the service type in the announcement is
wrong. Everything here is UDP.
"""

from __future__ import annotations

import socket
import struct

GROUP = "224.8.0.8"
PORT = 2920


def create_rx_socket(interface_ip: str) -> socket.socket:
    """Join the multicast group on one interface and return a listening socket.

    ``interface_ip`` must be the address of the interface facing the speakers
    (on a multi-homed host, binding the wrong one silently receives nothing).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", PORT))
    membership = struct.pack(
        "4s4s", socket.inet_aton(GROUP), socket.inet_aton(interface_ip)
    )
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    sock.setblocking(False)
    return sock


def create_tx_socket(
    interface_ip: str, ttl: int = 1, loopback: bool = False
) -> socket.socket:
    """Return a socket that emits to the group from one interface.

    ``ttl=1`` keeps the traffic on the local link. ``loopback`` is for tests.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    sock.setsockopt(
        socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip)
    )
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, int(loopback))
    return sock


def send(sock: socket.socket, payload: bytes) -> None:
    """Emit one datagram to the Ambilight Air group."""
    sock.sendto(payload, (GROUP, PORT))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_transport.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Add the no-Home-Assistant guard test**

The spec's structuring rule (§4.2) deserves a test rather than a promise. Create `tests/test_purity.py`:

```python
"""The protocol package must stay usable without Home Assistant."""

from pathlib import Path

PACKAGE = Path(__file__).parent.parent / "src" / "ambilight_air_protocol"


def test_no_module_imports_home_assistant():
    offenders = [
        path.name
        for path in PACKAGE.glob("*.py")
        if "homeassistant" in path.read_text()
    ]
    assert offenders == [], f"protocol layer must not import HA: {offenders}"
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -v`
Expected: PASS, all tests.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(transport): multicast RX/TX sockets with explicit interface binding

Interface selection is a required argument rather than a default: on a
multi-homed host, joining the group on the wrong interface receives
nothing and gives no error. Tests run over multicast loopback, so no
hardware is needed."
```

---

### Task 4: Live sniffer tool

**Files:**
- Create: `~/Projects/ha-ambilight-air/tools/sniff.py`
- Create: `~/Projects/ha-ambilight-air/docs/experiments/results.md`

**Interfaces:**
- Consumes: `decode`, `FrameError` (Task 1); `create_rx_socket` (Task 3).
- Produces: the `tools/sniff.py` CLI, used as the observation instrument by every experiment from Task 5 onward.

- [ ] **Step 1: Write the sniffer**

Create `tools/sniff.py`:

```python
"""Print decoded Ambilight Air frames as they arrive.

Usage:
    python tools/sniff.py [--interface IP] [--seconds N] [--raw]

Prints one line per frame: timestamp, sender, then the ten zones. With
--raw, prints the hex payload instead, for frames that fail to decode.
"""

from __future__ import annotations

import argparse
import selectors
import time

from ambilight_air_protocol.frame import FrameError, decode
from ambilight_air_protocol.transport import create_rx_socket


def format_frame(payload: bytes) -> str:
    try:
        frame = decode(payload)
    except FrameError as error:
        return f"UNDECODABLE ({error}): {payload.hex()}"
    zones = " ".join(
        f"{zone.red:5d},{zone.green:5d},{zone.blue:5d}" for zone in frame.zones
    )
    return f"{frame.sender} | {zones}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default="192.168.0.1")
    parser.add_argument("--seconds", type=float, default=0.0, help="0 = run forever")
    parser.add_argument("--raw", action="store_true")
    args = parser.parse_args()

    sock = create_rx_socket(args.interface)
    selector = selectors.DefaultSelector()
    selector.register(sock, selectors.EVENT_READ)

    started, count = time.monotonic(), 0
    print(f"listening on {args.interface}; Ctrl-C to stop")
    try:
        while True:
            if args.seconds and time.monotonic() - started >= args.seconds:
                break
            for _key, _mask in selector.select(timeout=0.5):
                payload, sender = sock.recvfrom(2048)
                count += 1
                elapsed = time.monotonic() - started
                body = payload.hex() if args.raw else format_frame(payload)
                print(f"[{elapsed:7.3f}] {sender[0]:15s} {body}")
    except KeyboardInterrupt:
        pass
    finally:
        selector.close()
        sock.close()

    elapsed = time.monotonic() - started
    rate = count / elapsed if elapsed else 0.0
    print(f"\n{count} frames in {elapsed:.1f}s ({rate:.1f} Hz)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify it decodes live traffic**

Turn the television **on** so it emits, then run:

```bash
cd ~/Projects/ha-ambilight-air
PYTHONPATH=src .venv/bin/python tools/sniff.py --seconds 10
```

Expected: a stream of lines with sender `788A866B7C5F0239`, ten zone triplets each, and a closing rate near 27 Hz. Zero `UNDECODABLE` lines — the decoder already handles everything this transmitter emits.

- [ ] **Step 3: Create the experiment log**

Create `docs/experiments/results.md`:

```markdown
# Experiment results

Every experiment records: date, what was done, what was observed, and the
conclusion drawn. Negative results are recorded too — knowing that something
does *not* work is a result.

Hardware under test: two Philips TAW6205 speakers, `192.168.0.68` and
`192.168.0.214`. Transmitter observed: Philips television at `192.168.0.183`,
sender field `788A866B7C5F0239`.

## E0 — Sniffer sanity check

- **Date:** (fill at run time)
- **Method:** `tools/sniff.py --seconds 10` with the television emitting.
- **Observed:** (frame count, rate, any undecodable frames)
- **Conclusion:** (decoder handles live traffic / does not)
```

- [ ] **Step 4: Fill in the E0 entry with the real numbers from Step 2**

Replace the placeholder lines with the observed frame count, rate, and undecodable count.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(tools): live sniffer, verified against the television's stream

Also starts the experiment log, which records negative results as
deliberately as positive ones."
```

---

### Task 5: Milestone 0 — can a forged frame drive a speaker?

**This is the go/no-go gate of the whole plan (spec §5.1).** Everything downstream that emits depends on the answer. Run it before writing any further code.

**Files:**
- Create: `~/Projects/ha-ambilight-air/tools/inject.py`
- Modify: `~/Projects/ha-ambilight-air/docs/experiments/results.md`

**Interfaces:**
- Consumes: `Frame`, `Zone`, `encode`, `ZONE_COUNT` (Tasks 1-2); `create_tx_socket`, `send` (Task 3).
- Produces: the `tools/inject.py` CLI — `--sender`, `--colour`, `--zone`, `--rate`, `--seconds` — reused by Tasks 6 to 9.

- [ ] **Step 1: Write the injector**

Create `tools/inject.py`:

```python
"""Emit synthetic Ambilight Air frames at a fixed rate.

Usage examples:
    # every zone mid-amber, impersonating the television, for 15 s
    python tools/inject.py --seconds 15

    # only zone 3, bright red
    python tools/inject.py --zone 3 --colour 4000,0,0 --seconds 15

    # a deliberately foreign sender identity
    python tools/inject.py --sender 001122334455ABCD --seconds 15

Run with the television powered off, so two transmitters never compete.
"""

from __future__ import annotations

import argparse
import time

from ambilight_air_protocol.frame import ZONE_COUNT, Frame, Zone, encode
from ambilight_air_protocol.transport import create_tx_socket, send

TV_SENDER = "788A866B7C5F0239"
RESTING = Zone(256, 256, 256)


def parse_colour(text: str) -> Zone:
    parts = text.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("colour must be R,G,B (decimal, 0..65535)")
    return Zone(*(int(part) for part in parts))


def build_frame(sender: str, colour: Zone, only_zone: int | None) -> Frame:
    if only_zone is None:
        zones = tuple(colour for _ in range(ZONE_COUNT))
    else:
        zones = tuple(
            colour if index == only_zone else RESTING
            for index in range(1, ZONE_COUNT + 1)
        )
    return Frame(sender=sender, zones=zones)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default="192.168.0.1")
    parser.add_argument("--sender", default=TV_SENDER)
    parser.add_argument("--colour", type=parse_colour, default=Zone(2048, 1024, 512))
    parser.add_argument("--zone", type=int, default=None, help="1..10, default all")
    parser.add_argument("--rate", type=float, default=27.0, help="frames per second")
    parser.add_argument("--seconds", type=float, default=15.0)
    args = parser.parse_args()

    payload = encode(build_frame(args.sender, args.colour, args.zone))
    sock = create_tx_socket(args.interface)
    interval = 1.0 / args.rate
    deadline = time.monotonic() + args.seconds
    sent = 0

    target = f"zone {args.zone}" if args.zone else "all zones"
    print(f"emitting {target} as {args.sender} at {args.rate} Hz for {args.seconds}s")
    try:
        while time.monotonic() < deadline:
            send(sock, payload)
            sent += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    print(f"sent {sent} frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Confirm the injector's own output decodes**

With the television **off**, in one terminal:

```bash
cd ~/Projects/ha-ambilight-air && PYTHONPATH=src .venv/bin/python tools/sniff.py --seconds 20
```

In a second terminal:

```bash
cd ~/Projects/ha-ambilight-air && PYTHONPATH=src .venv/bin/python tools/inject.py --seconds 15
```

Expected: the sniffer shows frames from `192.168.0.1` with all ten zones at `2048,1024,512`. This proves the emission path works before any conclusion is drawn about the speakers.

- [ ] **Step 3: Run the go/no-go experiment**

Television **off**. Look at the two speakers while running:

```bash
PYTHONPATH=src .venv/bin/python tools/inject.py --colour 8000,1000,1000 --seconds 20
```

Observe whether either speaker's LEDs change. Then repeat with `--colour 1000,1000,8000` to confirm any reaction tracks the colour sent rather than being coincidental.

- [ ] **Step 4: Record the outcome**

Append to `docs/experiments/results.md`:

```markdown
## E1 — Milestone 0: does a forged frame drive a speaker?

- **Date:** (fill at run time)
- **Method:** television off; `tools/inject.py` impersonating the television's
  sender identity, all zones driven to a saturated colour, then to a
  contrasting one.
- **Observed:** (per speaker: reacted / did not react; did the colour track?)
- **Conclusion:** (emission from a third party is accepted / rejected)
```

- [ ] **Step 5: Take the gate decision**

- **If at least one speaker reacts** → the emission path is viable. Continue to Task 6.
- **If neither reacts** → stop and report. Tasks 6, 7 and 9 become moot, since all three drive the speakers. Task 8 (colour calibration) and Task 10 (discovery metadata) stay fully valuable because both need only passive observation, and Task 11 still ships the specification. The Home Assistant integration would then be receive-only, and its plan must be rewritten around that. **Do not proceed to Task 6 without raising this with the user.**

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(tools): frame injector, plus milestone-0 go/no-go result

Records whether the speakers accept a frame that did not come from their
paired television -- the finding the emission path depends on."
```

---

### Task 6: Sender identity filtering

**Files:**
- Modify: `~/Projects/ha-ambilight-air/docs/experiments/results.md`

**Interfaces:**
- Consumes: `tools/inject.py` (Task 5), `tools/sniff.py` (Task 4).
- Produces: a recorded answer to "must we impersonate the television?", consumed by the integration plan.

- [ ] **Step 1: Run the three sender variants**

Television off. Each command runs for 15 s; note the speakers' reaction to each:

```bash
cd ~/Projects/ha-ambilight-air
# a) the television's identity
PYTHONPATH=src .venv/bin/python tools/inject.py --sender 788A866B7C5F0239 --colour 8000,1000,1000
# b) this host's MAC-shaped identity, arbitrary suffix
PYTHONPATH=src .venv/bin/python tools/inject.py --sender 001CC2A09EF70239 --colour 1000,8000,1000
# c) an obviously foreign identity
PYTHONPATH=src .venv/bin/python tools/inject.py --sender ZZZZZZZZZZZZZZZZ --colour 1000,1000,8000
```

- [ ] **Step 2: Probe the suffix**

The last four characters (`0239`) are unexplained. Re-run variant (a) with the suffix altered, keeping the MAC part intact:

```bash
PYTHONPATH=src .venv/bin/python tools/inject.py --sender 788A866B7C5F9999 --colour 8000,1000,1000
```

- [ ] **Step 3: Record the outcome**

Append to `docs/experiments/results.md`:

```markdown
## E2 — Sender identity filtering

- **Date:** (fill at run time)
- **Method:** four injections differing only in the 16-character sender field:
  the television's identity, a host identity, a foreign identity, and the
  television's MAC with an altered `0239` suffix.
- **Observed:** (which variants drove the speakers)
- **Conclusion:** (impersonation required or not; role of the suffix)
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs(experiments): sender identity filtering result"
```

---

### Task 7: Zone-to-speaker mapping

This experiment resolves the entity model the spec deliberately left open (§4.4).

**Files:**
- Modify: `~/Projects/ha-ambilight-air/docs/experiments/results.md`

**Interfaces:**
- Consumes: `tools/inject.py --zone` (Task 5).
- Produces: the zone→speaker mapping, which decides whether the integration exposes one light per speaker or a single global light.

- [ ] **Step 1: Sweep the ten zones one at a time**

Television off. For each zone, drive only that zone bright while the rest stay at the resting value, and note which speaker (if any) responds:

```bash
cd ~/Projects/ha-ambilight-air
for zone in 1 2 3 4 5 6 7 8 9 10; do
  echo "=== zone $zone ==="
  PYTHONPATH=src .venv/bin/python tools/inject.py --zone $zone --colour 12000,0,0 --seconds 8
  sleep 2
done
```

- [ ] **Step 2: Cross-check with two contrasting zones**

If the sweep suggests speaker A follows one zone and speaker B another, verify by driving both simultaneously in different colours. Run the two commands in parallel shells, or confirm the mapping by repeating the sweep in reverse order to rule out an ordering artefact.

- [ ] **Step 3: Record the outcome**

Append to `docs/experiments/results.md`:

```markdown
## E3 — Zone to speaker mapping

- **Date:** (fill at run time)
- **Method:** each of the ten zones driven bright in isolation, rest at the
  resting value; sweep repeated in reverse to exclude ordering artefacts.
- **Observed:** (zone -> speaker table, or "all zones affect both equally")
- **Conclusion:** speakers ARE / ARE NOT individually addressable.
- **Consequence for the integration:** one light entity per speaker, or a
  single global light entity (spec §4.4).
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs(experiments): zone-to-speaker mapping, settles the entity model"
```

---

### Task 8: Colour scale calibration

Resolves the spec's most uncertain point (§2.5, §5.2). Passive capture only — this task stays valuable even if Milestone 0 failed.

**Files:**
- Create: `~/Projects/ha-ambilight-air/tools/capture_stimulus.py`
- Modify: `~/Projects/ha-ambilight-air/docs/experiments/results.md`

**Interfaces:**
- Consumes: `decode` (Task 1), `create_rx_socket` (Task 3).
- Produces: `tools/capture_stimulus.py` (records per-zone min/mean/max for a labelled stimulus), and the measured answers to the two questions in spec §5.2.

- [ ] **Step 1: Write the stimulus capture tool**

Create `tools/capture_stimulus.py`:

```python
"""Capture the stream while a known image is displayed, and summarise it.

Usage:
    python tools/capture_stimulus.py black --seconds 8

Displays nothing itself: show the stimulus on the television, then run this
and it summarises what the transmitter emitted for that stimulus.
"""

from __future__ import annotations

import argparse
import selectors
import statistics
import time

from ambilight_air_protocol.frame import FrameError, decode
from ambilight_air_protocol.transport import create_rx_socket


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", help="name of the stimulus, e.g. black")
    parser.add_argument("--interface", default="192.168.0.1")
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()

    sock = create_rx_socket(args.interface)
    selector = selectors.DefaultSelector()
    selector.register(sock, selectors.EVENT_READ)

    samples: list[list[tuple[int, int, int]]] = []
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            for _key, _mask in selector.select(timeout=0.5):
                payload, _sender = sock.recvfrom(2048)
                try:
                    samples.append(list(decode(payload).zones))
                except FrameError:
                    continue
    finally:
        selector.close()
        sock.close()

    if not samples:
        print("no frames captured -- is the television emitting?")
        return 1

    print(f"\nstimulus: {args.label}   ({len(samples)} frames)")
    print("zone |      red mean  min   max |    green mean  min   max |     blue mean  min   max")
    for zone_index in range(len(samples[0])):
        row = [f" {zone_index + 1:>3} |"]
        for channel in range(3):
            values = [sample[zone_index][channel] for sample in samples]
            row.append(
                f" {statistics.mean(values):9.1f} {min(values):5d} {max(values):5d} |"
            )
        print("".join(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Capture the five stimuli**

Television **on**, Ambilight active. Display each image full-screen (a plain colour slide, a browser on a solid background, or a USB image), then run the matching command while it is displayed:

```bash
cd ~/Projects/ha-ambilight-air
PYTHONPATH=src .venv/bin/python tools/capture_stimulus.py black --seconds 8
PYTHONPATH=src .venv/bin/python tools/capture_stimulus.py white --seconds 8
PYTHONPATH=src .venv/bin/python tools/capture_stimulus.py red   --seconds 8
PYTHONPATH=src .venv/bin/python tools/capture_stimulus.py green --seconds 8
PYTHONPATH=src .venv/bin/python tools/capture_stimulus.py blue  --seconds 8
```

Save each table — they are the raw material of the conversion.

- [ ] **Step 3: Answer the two decisive questions**

From the captured tables:

1. **Does full-screen blue still satisfy `R > G > B`?** If yes, the three `u16` are not RGB channels and another colour space must be sought. If no, the red dominance seen in the reference capture was a property of the content, not of the protocol.
2. **Does full-screen black produce exactly `0x0100` on all three channels?** If yes, that identifies the resting value and explains the asymmetric floor recorded in spec §2.5.

- [ ] **Step 4: Record the outcome**

Append to `docs/experiments/results.md`:

```markdown
## E4 — Colour scale calibration

- **Date:** (fill at run time)
- **Method:** five full-screen stimuli (black, white, red, green, blue)
  captured with `tools/capture_stimulus.py`.
- **Observed:** (paste the five per-zone tables)
- **Q1 — blue keeps R > G > B?** (yes/no, and what it implies)
- **Q2 — black gives 0x0100 on all channels?** (yes/no)
- **Conclusion:** (units of the u16; the conversion rule to apply, or the
  remaining ambiguity if the stimuli did not settle it)
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(tools): stimulus capture, plus the colour scale calibration result

Settles the u16 units by controlled stimulus rather than by deduction --
the earlier deduction from four packets was disproved at 909 frames."
```

---

### Task 9: Silence behaviour and useful rate

**Files:**
- Modify: `~/Projects/ha-ambilight-air/docs/experiments/results.md`

**Interfaces:**
- Consumes: `tools/inject.py --rate/--seconds` (Task 5), `tools/sniff.py` (Task 4).
- Produces: the keep-alive interval, the way to turn the LEDs off, and the minimum acceptable frame rate — all three consumed directly by the integration's coordinator.

- [ ] **Step 1: Measure the silence timeout**

Television off. Drive the speakers, stop, and time how long the LEDs hold their last colour:

```bash
cd ~/Projects/ha-ambilight-air
PYTHONPATH=src .venv/bin/python tools/inject.py --colour 12000,2000,2000 --seconds 10
# then watch the speakers with a stopwatch and note when they go dark
```

- [ ] **Step 2: Test turning off by zeroing**

```bash
PYTHONPATH=src .venv/bin/python tools/inject.py --colour 0,0,0 --seconds 10
```

Note whether zeros extinguish the LEDs, or whether they hold at the resting value.

- [ ] **Step 3: Find the minimum acceptable rate**

Drive a colour at descending rates and note the lowest one with no visible flicker or dropout:

```bash
for rate in 27 15 10 5 2 1; do
  echo "=== ${rate} Hz ==="
  PYTHONPATH=src .venv/bin/python tools/inject.py --colour 8000,4000,1000 --rate $rate --seconds 12
done
```

- [ ] **Step 4: Record the outcome**

Append to `docs/experiments/results.md`:

```markdown
## E5 — Silence behaviour, off-switch and rate

- **Date:** (fill at run time)
- **Silence timeout:** (seconds from last frame to LEDs going dark, or
  "they hold indefinitely")
- **Turning off:** (zeros extinguish / zeros hold at resting / other)
- **Minimum flicker-free rate:** (Hz)
- **Conclusion:** keep-alive interval and off-strategy for the coordinator.
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs(experiments): silence timeout, off-switch and minimum rate"
```

---

### Task 10: Discovery metadata and the HTTP endpoint

Closes the remaining unexplained fields listed in spec §5.2 and §10: the speakers' HTTP server on port 80, the `seed=2` mDNS key, and which of the two Philips televisions is the transmitter. None of these blocks the emission path, but the specification is incomplete without them.

**Files:**
- Modify: `~/Projects/ha-ambilight-air/docs/experiments/results.md`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure probing with standard tools).
- Produces: recorded answers about the HTTP endpoint's role, the `seed` key and the transmitter's identity, all consumed by the specification in Task 11.

- [ ] **Step 1: Probe the HTTP endpoint's verbs**

Every GET returns the same 66-byte `AL Air` / `OK_GET` stub regardless of path, which suggests the response is a fixed stub and the real behaviour lies elsewhere. Test the other verbs:

```bash
for verb in GET POST PUT OPTIONS DELETE; do
  echo "=== $verb ==="
  curl -s -i -m 5 -X $verb http://192.168.0.68/ | head -6
done
echo "=== POST with a body ==="
curl -s -i -m 5 -X POST -d 'test' http://192.168.0.68/ | head -6
```

Note whether any verb produces a response differing from `OK_GET` (for example `OK_POST`), which would reveal a control channel.

- [ ] **Step 2: Re-read the mDNS advertisement in full**

```bash
timeout 20 avahi-browse -r -t _tpv_al._tcp
```

Record every TXT key and value for both speakers. Compare the two: a key that differs between them identifies devices, while one that is identical is a protocol constant.

- [ ] **Step 3: Identify the transmitting television**

```bash
curl -s -m 8 http://192.168.0.183:1925/6/system | head -20
```

The JointSpace `system` endpoint returns the model name and serial. Match it against the two televisions advertised in mDNS ("Salle TV" and "Salon") to establish which one transmits.

- [ ] **Step 4: Record the outcome**

Append to `docs/experiments/results.md`:

```markdown
## E6 — Discovery metadata and the HTTP endpoint

- **Date:** (fill at run time)
- **HTTP verbs:** (which verbs answer, and whether any response differs from
  the `OK_GET` stub)
- **mDNS TXT keys:** (full key/value list for both speakers, and which keys
  differ between them)
- **Transmitting television:** (model and name from the JointSpace endpoint)
- **Conclusion:** (role of the HTTP server; meaning of `seed`, if determined,
  or recorded as still unknown)
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs(experiments): HTTP endpoint, mDNS metadata and transmitter identity"
```

---

### Task 11: Publish the protocol specification and wire up CI

The specification is the deliverable with the longest life (spec §8): code ages, a correct spec does not. It is also the foundation of the phase-2 relay to the Yeelight and Meross lamps.

**Files:**
- Create: `~/Projects/ha-ambilight-air/docs/protocol.md`
- Create: `~/Projects/ha-ambilight-air/README.md`
- Create: `~/Projects/ha-ambilight-air/.github/workflows/ci.yml`

**Interfaces:**
- Consumes: every result recorded in `docs/experiments/results.md` (Tasks 4-10).
- Produces: the published specification, consumed by the integration plan and by phase 2.

- [ ] **Step 1: Write the CI workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install pytest ruff mypy
      - run: ruff check .
      - run: ruff format --check .
      - run: mypy src
      - run: pytest -v
```

- [ ] **Step 2: Make the checks pass locally**

```bash
cd ~/Projects/ha-ambilight-air
.venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/mypy src && .venv/bin/pytest -v
```

Fix anything reported. Expected end state: all four commands clean.

- [ ] **Step 3: Write `docs/protocol.md`**

Structure it as follows, filling every section from the recorded experiment results — no section may be left speculative where an experiment answered it, and any question the experiments did **not** settle must be stated as open rather than guessed:

```markdown
# Ambilight Air protocol

Philips televisions broadcast their Ambilight colours to compatible devices
over the local network. The protocol is undocumented; this specification was
established by observation and controlled experiment, and is validated by a
round-trip test over 909 captured frames.

## 1. Discovery
mDNS service, port, TXT keys, and the `_tcp`/UDP discrepancy.

## 2. Transport
Multicast group, port, frame rate, and the inactive unicast channel on 2930.

## 3. Frame format
The byte table, the record layout, and the arithmetic that closes at 120.

## 4. Colour encoding
The measured units, the conversion rule, and the stimuli that established them.

## 5. Sender identity
The 16-character field, whether it is enforced, and the role of the suffix.

## 6. Zones
How many, and how they map onto physical devices.

## 7. Timing
Transmitter rate, minimum usable rate, silence timeout, how to turn off.

## 8. Open questions
Anything the experiments did not settle, stated plainly.

## 9. Tested hardware
Philips TAW6205 speakers; transmitter model and firmware if known.

## 10. Method
How this was established, and how to reproduce it with the tools in this
repository.
```

- [ ] **Step 4: Write `README.md`**

It must state honestly what is verified and what is not:

```markdown
# ha-ambilight-air

Reverse-engineered support for **Philips Ambilight Air**, the feature by which
a Philips television broadcasts its Ambilight colours to compatible devices on
the local network.

This repository currently contains the **protocol library and tooling**. A Home
Assistant integration is being built on top of it.

- [Protocol specification](docs/protocol.md)
- [Experiment log](docs/experiments/results.md)

## Status

Verified against two Philips TAW6205 speakers and one Philips television. Other
hardware is untested — reports welcome.

## Tools

    python tools/sniff.py --seconds 10          # watch the live stream
    python tools/inject.py --colour 8000,0,0    # drive the devices
    python tools/capture_stimulus.py white      # calibration capture

## Requirements

Python 3.13+. The devices use IP multicast, so the host must be on the same
layer-2 network as them; a container in bridged networking will not receive the
stream.

## Licence

Apache-2.0.
```

- [ ] **Step 5: Verify the spec against a fresh capture**

Reproduce the protocol document's claims once more from a new capture, to catch anything written from memory rather than from data:

```bash
cd ~/Projects/ha-ambilight-air
PYTHONPATH=src .venv/bin/python tools/sniff.py --seconds 15
```

Confirm the rate, sender and zone count match what `docs/protocol.md` states.

- [ ] **Step 6: Push to GitHub**

```bash
cd ~/Projects/ha-ambilight-air
gh repo create mallanic/ha-ambilight-air --public --source=. --remote=origin \
  --description "Reverse-engineered Philips Ambilight Air protocol and tooling"
git add -A
git commit -m "docs: protocol specification, README and CI"
git push -u origin main
```

- [ ] **Step 7: Confirm CI is green**

```bash
gh run watch
```

Expected: the `quality` job passes. A red run here blocks the integration plan — it is the same pipeline the integration will extend.

---

## What comes next

With Tasks 1-11 complete, the following are known facts rather than assumptions:

- whether the speakers accept a third-party transmitter (Task 5) — and therefore whether the integration can control them at all;
- whether impersonation is required (Task 6);
- whether the speakers are individually addressable (Task 7) — and therefore whether the integration exposes one light entity per speaker or one global entity;
- what the `u16` values mean (Task 8) — and therefore how Home Assistant colours convert to the wire;
- the keep-alive interval, the off-switch and the minimum rate (Task 9) — the coordinator's timing parameters.

The Home Assistant integration plan is written **after** these land, using them as inputs. It will cover the config flow with zeroconf discovery, the shared coordinator, the light and binary-sensor entities, the two error paths of spec §6.4, translations, `hacs.json`, `quality_scale.yaml`, and the HACS and hassfest validation actions.

One decision is deferred to that plan: how the protocol package reaches the integration — vendored inside `custom_components/ambilight_air/`, or published to PyPI and declared in the manifest's `requirements`. Both are viable; the choice depends on whether core submission is pursued immediately, and it does not affect any task above.
