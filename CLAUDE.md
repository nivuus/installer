# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Instructions for Claude

**IMPORTANT**: Whenever you learn something important about this project (architecture decisions, critical bugs fixed, configuration patterns, etc.), immediately update this CLAUDE.md file. Keep it:
- **Compact**: Dense, relevant information only
- **No duplicates**: Remove redundant information
- **Up-to-date**: Reflect current project state

**BE PROACTIVE**: When working on the codebase, actively look for improvements or issues beyond the current scope. If you spot bugs, performance issues, code smells, security concerns, or optimization opportunities - signal them to the user and fix them. Don't wait to be asked.

## Project Overview

**Nivuus** is a cloud gaming server infrastructure with comprehensive system monitoring integration. The project consists of:

1. **Installer** (`installer/`): Bootable ISO that installs Nivuus via a web wizard served over a WiFi setup hotspot. The unattended Windows LTSC guest build for GPU passthrough now lives at `console/guest/` (moved out of `installer/windows-guest/`, which no longer exists) — see "Installer Architecture" below.
2. **Infrastructure Configuration** (`scripts/`, `configs/`): thermal/RAPL policy, VM CPU partitioning, GPU passthrough hooks, PCIe guard, disk maintenance, VM wake-on-demand
3. **Host documentation** (`docs/`): system audit, VM configuration, thermal campaign, and the superpowers specs/plans

### Sibling repositories (split out of this one on 2026-08-26)

The suite lives under the `nivuus` GitHub organisation; the local working tree mirrors it
as `~/Projects/Nivuus/packages/<name>`.

| Repository | What it is |
|---|---|
| `nivuus/mqtt` | TypeScript agent publishing host metrics to Home Assistant via MQTT Discovery |
| `nivuus/marketplace` | HA integration `docker_marketplace` + YAML app catalog |
| `nivuus/desk` | Browser-based remote desktop (Node.js + Rust) |
| `nivuus/home-stock` | Garde-manger — household stock as an HA integration |
| `nivuus/shell` | ZSH environment |
| `nivuus/design` | Brand identity, mockups and design tokens |
| `nivuus/media-manager` | Médiathèque (Plex, *arr, Tdarr) — package `nivuus.dev/v1`, tier `userspace` |
| `nivuus/home-agent` | **archived** — autonomous AI agent (Gemini + ChromaDB) |
| `nivuus/voice-agent` | **archived** — OpenAI-compatible shim adding HA tool-calling to ollama |

**Knowledge that moved with the code**: the MQTT agent's architecture, entity-naming rules
and per-feature notes (`HostapdManager`, `PppoeCredentials`, `FirewallManager`) now live in
`nivuus/mqtt`'s own CLAUDE.md, which carries a *Host Context* section repeating the minimum
host facts its code depends on. Everything about the host itself stayed here.

## Installer Architecture (`installer/`)

Bootable **Debian live ISO** (built with `live-build`) that installs Nivuus onto
a target disk via a **web wizard served over a WiFi setup hotspot**. Flow: boot
USB → `nivuus-ap.service` opens AP `Nivuus-Setup-XXXX` (10.42.0.1, captive DNS;
falls back to Ethernet DHCP if no AP-capable WiFi) → `nivuus-portal.service`
(FastAPI :80) shows the wizard → on submit, `install-engine/run.py` does a
scripted debootstrap install. The live root runs in RAM; the engine never writes
the live image to disk. Full docs: `installer/README.md`.

**Components:**
- `installer/common/hardware.py` — generic detection (disks, NICs, WiFi
  AP-capability via `iw`, GPU vendor:device IDs for `vfio-pci.ids`, CPU topology
  → computed `isolcpus`/`nohz_full`, **not** hardcoded to the i9-12900K).
- `installer/common/progress.py` — jsonl progress protocol (durable backlog +
  stdout) shared by engine and portal; WebSocket clients tail it for free
  reconnection. Override dir via `NIVUUS_PROGRESS_DIR` (default `/run/nivuus-install`).
- `installer/install-engine/` — `run.py` orchestrator + `steps/` (partition,
  debootstrap, chroot_base, bootloader, features, validate). `--stop-after STEP`
  for staged testing. `templates/*.j2` render NM bridges, VLAN, PPPoE, hostapd.
  `chroot_base` also lays the **memory guard on every host, whatever the
  wizard selected**: `earlyoom` in `CORE_PACKAGES` (fails the install loudly)
  + `/etc/default/earlyoom` from `EARLYOOM_ARGS` (`-m 10,5 -s 100,100`, `--avoid`
  on infrastructure, no `--prefer`). Added after the 2026-09-27 hang: orphaned
  vitest workers took ~28 GB, swap full, 45 min of thrashing with no OOM kill.
  earlyoom, not systemd-oomd: oomd kills a whole cgroup (every SSH session in
  a `session-N.scope`). Suite: `scripts/tests/test_install_engine_base.py`.
- `installer/webapp/` — FastAPI portal: `main.py` (routes + `/ws/progress` +
  captive-detection endpoints), `models.py` (Pydantic v2 `InstallConfig`),
  `installer_runner.py`, `static/` + `templates/` wizard.
- `installer/ap/bring-up-ap.sh` — hotspot bring-up + captive nftables redirect.
- `installer/iso-build/` — live-build config; hook `0500-nivuus-venv` builds a
  pydantic-v2 venv (bookworm ships v1), hook `9000` enables the services.


## Build and test

**Build & test:** `cd installer && sudo make build-iso` (needs `live-build`).
`make test-portal` (portal on :8080), `make test-vm` (QEMU UEFI, portal via
Ethernet fallback — WiFi AP isn't emulable in QEMU), engine on a loopback image
via `--stop-after`. The riskiest path (partition/format/mount) is validated; the
debootstrap path uses standard tooling.

**IMPORTANT**: The project is in development mode - do NOT install packages unless explicitly required.

## Code Style Guidelines

From `.github/copilot-instructions.md`:

- **File Organization**: Maximum 200 lines per file - split if larger
- **Architecture**: Use classes and inheritance extensively
- **Modularity**: Each file should be self-contained and minimal
- **Comments**: English only
- **Logging**: Use logger for debugging, remove logs when no longer needed
- **Workflow**: Build → Start → Check logs → Fix → Repeat
- **Autonomy**: Be proactive - execute commands without asking for approval
- **System Adaptation**: Understand and adapt to the actual machine configuration

## Related Documentation

- **Main README**: `/README.md` - Project overview and installation
- **Installer**: `/installer/README.md` - ISO build, portal, install engine
- **System Audit**: `/docs/system-audit.md` - Complete infrastructure documentation
- **Network Config**: `/configs/network/` - NetworkManager and hostapd setup
- **Firewall Config**: `/configs/firewall/` - firewalld and nftables rules
- **VM Config**: `/docs/vm-configuration.md` - QEMU/KVM setup with GPU passthrough
- **Specs & plans**: `/docs/superpowers/` - design documents and implementation plans
- **Console debts**: `/docs/console-dettes.md` - known gaps on the Windows guest
  (gamepad rumble, X360-only pad so no motion sensor, off-brand wallpaper). Its
  counterpart is `docs/dettes.md` in `nivuus/retro`; three of those debts cross
  the repo boundary, the pad being created here and consumed there.
- **MQTT agent**: now `nivuus/mqtt` - its CLAUDE.md holds the agent-side documentation

## Detailed documentation (load on demand)

The detail below was moved out of this file to keep it small. It is NOT imported: read a file only when the task touches its subject.

### Installer, console package, package engine

- [docs/claude/installer-console-package.md](docs/claude/installer-console-package.md) - read when touching `console/` (manifest, hooks `install.py`/`activate.py`, AppArmor hook placement, `dedicated_nvme` selection, activate phase 2d, guest-ready timer, named residuals).
- [docs/claude/console-guest-provisioning.md](docs/claude/console-guest-provisioning.md) - read when touching Windows guest provisioning stages 30/33/34 (Gaming Services, Xbox stack, explorer shell, `Run\Steam`) or `PROVISION_VERSION`.
- [docs/claude/console-activate-lessons.md](docs/claude/console-activate-lessons.md) - read when touching `guest_steps.py`, `guest-ready-watch.py`, `domain.py`, or the wipe/disk-mode guards; lessons on test doubles and guards.
- [docs/claude/package-engine.md](docs/claude/package-engine.md) - read when touching `installer/packages/` (resolve/install/activate contract, facts, activation stamp, kernel-parameter validation, wizard gap, suite counts).

### Commands and host tooling

- [docs/claude/dev-commands.md](docs/claude/dev-commands.md) - read for the full command block: ISO build, staged engine run, Windows guest scripts, `retro_sync.py` refusals, host script tests.
- [docs/claude/host-shell-gotchas.md](docs/claude/host-shell-gotchas.md) - read before running shell commands on the host (zsh profile hangs, `grep` function, `systemctl` unusable from a Claude session, D-Bus workaround).
- [docs/claude/home-assistant-cli.md](docs/claude/home-assistant-cli.md) - read before changing Home Assistant configuration (`ha` CLI usage; never edit `.storage/`).

### Host infrastructure (the reference server)

- [docs/claude/host-infrastructure.md](docs/claude/host-infrastructure.md) - read for boot chain (systemd-boot), CPU thermal/RAPL and fan policy, CPU partitioning, GPU ownership, libvirt hook traps, hypervisor/network/docker basics.
- [docs/claude/host-trixie-upgrade.md](docs/claude/host-trixie-upgrade.md) - read for Debian 13 upgrade fallout (SSH/utmp, virtiofsd, OVMF, VM shutdown, MQTT credentials, fail2ban, SMB hardening, clamonacc, Claude sessions during system ops).
- [docs/claude/host-ops-audits.md](docs/claude/host-ops-audits.md) - read for energy/perf pass, disk space bounds, security follow-ups audit, HA service registration trap.
- [docs/claude/host-cloud-gaming-apollo.md](docs/claude/host-cloud-gaming-apollo.md) - read for Apollo/Sunshine + SudoVDA streaming, HDR, Moonlight, and the Dockerised Ollama API.
- [docs/claude/host-stability-incidents.md](docs/claude/host-stability-incidents.md) - read when the host freezes, halts or swaps (hard-freeze investigation, swap thrashing outage).
- [docs/claude/host-network-rf-wan.md](docs/claude/host-network-rf-wan.md) - read for RF/2.4 GHz coexistence, VM wake-on-demand, and WAN/PPPoE (Orange) recovery.
