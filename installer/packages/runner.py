"""Executing a package's hooks, and the jsonl protocol they speak.

`resolve` being READ-ONLY is the load-bearing property of this module. It runs
before the engine writes anything, so the exact kernel command line is known
before partitioning - which is why `bootloader` can stay exactly where it is in
run.py instead of being reordered after the features step.

The protocol is deliberately a subprocess speaking jsonl on stdout rather than
an imported Python API. A package must be able to run on a Debian that has
never seen this engine (the standalone path), so it cannot import from here;
and a stranger's code running in the installer's own process is a worse idea
than a pipe.

Events a hook may emit, one JSON object per line:
    {"event":"progress","pct":int,"msg":str}
    {"event":"platform","kernel-cmdline":[...],"modules":[...],
     "hugepages-mib":int}          - resolve only
    {"event":"refuse","reason":str}                  - resolve only
    {"event":"done"}
Anything else on stdout is relayed as a progress line rather than dropped: a
hook that prints is easier to debug than a hook that is silently truncated.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass

from .manifest import Manifest, Platform

# A hook is third-party code; it never gets to hang the install forever.
HOOK_TIMEOUT = {"resolve": 120, "install": 1800, "activate": 7200}


class HookError(RuntimeError):
    """Raised when a hook fails, times out, or speaks an unusable protocol."""


@dataclass(frozen=True)
class Resolution:
    ok: bool
    reason: str
    platform: Platform


def _context(manifest: Manifest, hw: dict, answers: dict) -> str:
    return json.dumps({
        "package": {"name": manifest.name, "version": manifest.version,
                    "root": manifest.root},
        "hw": hw,
        "answers": answers,
    })


def _run_hook(manifest: Manifest, phase: str, hw: dict, answers: dict,
              root: str = "", emit=None) -> list[dict]:
    """Run one hook and return the events it emitted. [] when none is declared."""
    hook = manifest.hook_path(phase)
    if not hook:
        return []
    if not os.path.isfile(hook):
        raise HookError(
            f"package {manifest.name}: hook '{phase}' declared but missing at {hook}")

    cmd = [sys.executable, hook, "--phase", phase]
    if root:
        cmd += ["--root", root]

    try:
        proc = subprocess.run(
            cmd, input=_context(manifest, hw, answers), capture_output=True,
            text=True, timeout=HOOK_TIMEOUT[phase], cwd=manifest.root, check=False)
    except subprocess.TimeoutExpired as exc:
        raise HookError(
            f"package {manifest.name}: hook '{phase}' exceeded "
            f"{HOOK_TIMEOUT[phase]}s and was killed") from exc

    events: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Not protocol: relay it rather than drop it. A hook that prints
            # is far easier to debug than one whose output vanished.
            if emit:
                emit.info("packages", 0, f"[{manifest.name}] {line[:120]}")
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        if emit and event.get("event") == "progress":
            emit.info("packages", int(event.get("pct") or 0),
                      f"[{manifest.name}] {str(event.get('msg', ''))[:120]}")

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else "no stderr"
        raise HookError(
            f"package {manifest.name}: hook '{phase}' exited {proc.returncode} "
            f"({tail})")
    return events


def run_resolve(manifest: Manifest, hw: dict, answers: dict,
                emit=None) -> Resolution:
    """Read-only phase. Returns the merged platform block, or a refusal."""
    events = _run_hook(manifest, "resolve", hw, answers, emit=emit)

    resolved = Platform()
    for event in events:
        kind = event.get("event")
        if kind == "refuse":
            reason = str(event.get("reason") or "").strip() \
                or "le package a refusé cette machine sans en donner la raison"
            return Resolution(ok=False, reason=reason, platform=manifest.platform)
        if kind == "platform":
            hugepages = event.get("hugepages-mib") or 0
            resolved = Platform(
                kernel_cmdline=tuple(str(v) for v in
                                     (event.get("kernel-cmdline") or [])),
                modules=tuple(str(v) for v in (event.get("modules") or [])),
                hugepages_mib=int(hugepages) if isinstance(hugepages, int) else 0,
            )
    return Resolution(ok=True, reason="",
                      platform=manifest.platform.merge(resolved))


def run_install(manifest: Manifest, hw: dict, answers: dict, root: str,
                emit=None) -> None:
    """Apply the package to the filesystem at `root`. Raises HookError."""
    _run_hook(manifest, "install", hw, answers, root=root, emit=emit)


def run_activate(manifest: Manifest, hw: dict, answers: dict, emit=None) -> None:
    """Post-reboot phase, on the live system with network. Raises HookError."""
    _run_hook(manifest, "activate", hw, answers, emit=emit)
