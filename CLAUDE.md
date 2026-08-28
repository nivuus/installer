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

1. **Installer** (`installer/`): Bootable ISO that installs Nivuus via a web wizard served over a WiFi setup hotspot, plus `windows-guest/` (unattended Windows LTSC guest build for GPU passthrough)
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
- `installer/webapp/` — FastAPI portal: `main.py` (routes + `/ws/progress` +
  captive-detection endpoints), `models.py` (Pydantic v2 `InstallConfig`),
  `installer_runner.py`, `static/` + `templates/` wizard.
- `installer/ap/bring-up-ap.sh` — hotspot bring-up + captive nftables redirect.
- `installer/iso-build/` — live-build config; hook `0500-nivuus-venv` builds a
  pydantic-v2 venv (bookworm ships v1), hook `9000` enables the services.

**`install.sh` is gone (2026-08-27).** Five of its seven blocks were VM
setup and now live in the `console` package (`console/nivuus-package.yaml`
plus `console/hooks/`); the thermal block became an ordinary
`install-engine` feature. The `NIVUUS_DIR` / `NIVUUS_IN_CHROOT` /
`NIVUUS_ISOLCPUS` / `NIVUUS_VFIO_IDS` env plumbing existed only to make that
script runnable inside a chroot and went with it.

**`console/` is the first Nivuus package, and it is deliberately ordinary.**
It declares `tier: platform`, claims `gpu` and `nvme` exclusively, and
requires the `iommu`, `gpu-discrete` and `nvme-dedicated` capabilities. Two
things about it are easy to break:

* **`vm-cpu-partition.sh` MUST be deployed under `/etc/libvirt/hooks/`**, and
  `console/hooks/install.py` does so. The libvirtd AppArmor profile grants
  `/etc/libvirt/hooks/** rmix`, so hooks run *inheriting* that profile, which
  allows exec of `/bin`, `/sbin`, `/usr/bin` and `/usr/sbin` — but **not**
  `/usr/local/sbin`. Installed there instead it dies at VM start with a
  misleading `bad interpreter: Permission denied` and no DENIED line in dmesg.
* **`nivuus-cpu-mode@{gaming,idle}.service` is a public contract of this
  repository.** The thermal policy stays here (it is a host policy), but its
  modes are driven by the package's libvirt hooks. The package calls the unit
  if it exists and does nothing if it does not — which is what lets it install
  on a Debian that has never seen this installer.
* **The `dedicated_nvme` ANSWER chooses the passthrough disk; the host-root
  exclusion is only an assertion on that choice.** Getting this backwards
  broke the package's primary path outright: a selector that picks "the NVMe
  not backing the host root" has nothing to work with on a live ISO — the
  root is the live image, `findmnt -no SOURCE /` says `overlay`, no PCI disk
  backs it — so it refused on *every* machine with `host root not
  identified`. Hardware checks run on an already-installed host pass happily
  and prove nothing about it. `select_passthrough_nvme()` therefore takes
  `host_addresses=None` as "the assertion does not apply", never as a
  refusal, and only falls back to auto-selection when no answer was given.
  The engine adds the one check the package structurally cannot make —
  `plan_packages()` refuses a `disque` answer naming the **install target**,
  because a hook receives `hw` and its own answers but never the install
  config.
* **`console/hooks/install.py` now places the whole host-side lifecycle**
  (2026-08-28, tasks 1-4 of `2026-08-28-console-cablage-hote`): the libvirt
  dispatcher, the two GPU hooks, the `rules.sh` pair, `vm-cpu-partition.sh`,
  the two generated CPU wrappers, four host scripts (`vm-wake-gate.py`,
  `handle-vm-start.sh`, `winvm`, and `vm-idle-shutdown.sh`), six systemd
  units plus their shared no-start-limit drop-in copied into two `.d/`
  directories, and `retro.json`. `console/hooks/activate.py` is no longer a
  stub: it arms three of those six units (the two wake sockets and the
  idle-shutdown timer) with a symlink into their `.wants/` directory. The
  three hugepage hooks (`00-set-hugepages.sh`, `00-hugepages-fix.sh`,
  `hugepages-reset.sh`) were **deleted**, not deployed — the hugepage pool
  is already set declaratively by the engine from the manifest's
  `hugepages-mib`, so deploying them would have done nothing; documenting
  them as "to be wired" had already misled the README once. The one thing
  still missing: **`vm-idle-shutdown.sh` was never versioned anywhere before
  this plan** — same class of gap as `handle-vm-start.sh` before
  2026-08-24, a script that existed only deployed on the production host.
  Both are now source in `console/host/`. The console is **still not
  functional from an install alone**: `activate` arms the wake/idle units
  but does not build the Windows guest — it can manage a `Windows` domain
  if one already exists, not create one. That is phase 2c
  (`installer/windows-guest/` folding into this package, `domain.py`
  repaired, `activate` building the VM), tracked separately.

`hardware.py` is now split by that same principle: `installer/common/hardware.py`
detects **capabilities** (coarse: is there an IOMMU, a discrete GPU, a spare
NVMe — `list_gpus` no longer carries `ids`, `cpu_topology` no longer carries
`isolcpus`), and `console/hardware.py` detects the **details** in its resolve
phase.

**`domain.py` is DEAD until phase 2c, dated 2026-08-27 — measured, not
inferred.** Phase 2b (2026-08-28, `console/hooks/install.py` + `activate.py`
wiring the rest of the host-side lifecycle) did not touch it — it was out of
that plan's scope and stays deferred. Both `domain.py xml` and `domain.py
define` fail at `main()`'s entry, before any hardware is touched:

```
main -> ImportError cannot import name 'HardwareError' from 'common.hardware'
build_domain_xml -> AttributeError module 'common.hardware' has no attribute 'passthrough_nvme'
```

`main()` does `from common.hardware import HardwareError` (`domain.py:263`)
*before* the try block, so **ImportError at entry is the real failure mode** —
the AttributeError deeper in `build_domain_xml()` (`domain.py:233`) is only
reachable by calling that function directly. `HardwareError`,
`passthrough_nvme()` and `pci_slot_functions()` all moved to
`console/hardware.py` in this same phase; both imports are lazy, inside the
functions, which is *why* merely importing the module does not fail.

Neither is a live command: `python3 installer/windows-guest/domain.py` in the
Development Commands below is listed for phase-2c context only — it cannot
run today.

The aggregator now carries a **failing marker** for it:
`test_windows_guest_production_domain.py` asserts that `main()` raises
`ImportError`. That test will itself fail the day someone repairs
`domain.py` — which is the point: remove the marker in the same change as the
repair. Everything else in that suite calls `domain_xml()` with explicit
keyword arguments and never goes near hardware detection, and
`test_windows_guest_domain.py` imports `testdomain.py` (the throwaway LTSC
generator), not `domain.py` — which is how a green 26-suite run used to say
nothing at all about this path.

**Package engine (2026-08-27)**: `installer/packages/` implements the
`nivuus.dev/v1` contract — a declarative `nivuus-package.yaml` plus three hooks
(`resolve`/`install`/`activate`). Packages are sibling repositories embedded
into the ISO via `PACKAGE_REPOS=… make build-iso` and discovered at
`/opt/nivuus-packages/*/` (override with `NIVUUS_PACKAGES_DIR`).

Three properties carry the design and are easy to break by accident:
* **`resolve` is read-only and runs before `partition`.** That is the only
  reason `bootloader` can stay where it is in `run.py`: the kernel cmdline is
  known before the disk is touched. Moving a package's cmdline contribution
  later would force the whole pipeline to be reordered.
* **`iommu` is read from the ACPI tables (`DMAR`/`IVRS`), never from
  `/sys/kernel/iommu_groups`.** The live ISO boots *without* `intel_iommu=on` —
  adding it is exactly what a passthrough package asks for — so a check on the
  active state would answer "no" on every capable machine and no such package
  would ever be offered.
* **The engine detects capabilities, the package detects details.** The engine
  must answer `requires.capabilities` before running any hook, so it cannot
  delegate. It stays coarse (`iommu`, `gpu-discrete`, `nvme-dedicated`,
  `cpu-hybrid`); the precise work — PCI functions, IOMMU groups, `vfio-pci.ids`
  — belongs to `resolve`.

Two more findings from implementing the engine, both worth knowing before
touching it:
* **`resolve` being read-only is a CONVENTION, not a sandbox.** Tested
  directly: a `resolve` hook that writes to disk succeeds, and the write
  persists — nothing chroots or restricts the subprocess. What the pipeline
  ordering actually depends on is that the engine never *asks* `resolve` to
  write and never *uses* anything it wrote, not that `resolve` is incapable. A
  malicious package owns the machine from the `install` phase onward
  regardless, since that runs as root. The rule protects against accident and
  ordering mistakes — real, worth having — but must be described as that, not
  as a security boundary. Real sandboxing would be its own project.
* **`chroot_base.py:9` imports `crypt`, removed from the Python stdlib in 3.13
  (PEP 594).** Used for exactly one thing (line 98): hashing the user
  account's password. The live ISO builds on **bookworm**
  (`iso-build/auto/config --distribution bookworm`), Python 3.11, so
  production is unaffected today — but `run.py` cannot even be imported on a
  Python 3.13 host, and the day the ISO base moves to trixie the installer
  breaks at the step that creates the user account, at the very end of an
  otherwise successful install. Remedies: `passlib`, `openssl passwd -6`, or
  `chpasswd -e` inside the chroot. Dated technical debt, not a bug to fix now
  — out of the package-engine plan's scope.

Activation uses a stamp (`/var/lib/nivuus/packages/<name>.activated`), not a
self-disabling unit: an interrupted activation must retry at the next boot
rather than believe it succeeded.

**Arming `activate` takes three copies onto the target, and the whole phase is
dead if any is missed** (it was, on the first cut of this branch, while the
install still reported success — `systemctl enable` ran with `check=False`).
`apply_packages()` does all three: it copies
`configs/systemd/nivuus-package-activate@.service` out of the payload into
`{target}/etc/systemd/system/`, chmods `activate_cli.py` executable **in
place** at `/opt/nivuus/installer/packages/` — the unit's `ExecStart` points
there, *not* at `/usr/local/sbin`, because the script computes its own
`sys.path` from `__file__` and a copy elsewhere cannot import `common`/`packages` —
and copies each **selected** package's directory to
`{target}/opt/nivuus-packages/<name>/` (the live medium's copy is on the LIVE
root and does not survive the reboot). Enablement is a **direct symlink** into
`multi-user.target.wants/`, which is precisely what `systemctl enable` does for
a `WantedBy=multi-user.target` template unit — chosen over shelling into the
chroot because `systemctl` fails silently in constrained environments (see the
PID-namespace note below) and a symlink either exists or raises.

**A fourth broken link, found by re-review after the first three were fixed:
`python3` itself was never guaranteed on the target.** `debootstrap.py`'s
`BASE_INCLUDE` has no `python3`, and the only other installer of it
(`install.sh`'s `python3-pip`) runs only behind the optional `kvm-vfio`/
`thermal` features — so a minimal install that selects a package but neither
feature reached first boot with no interpreter for the unit's own
`ExecStart`, silently, forever (the apt call was `check=False`, and the
stamp file is only written on success, so it retried every boot with no
error surfaced anywhere). Fixed by splitting the one apt call into two:
`ACTIVATE_APT = ["python3", "python3-yaml"]` (the activate phase's own hard
requirements — `activate_cli.py` needs an interpreter to run at all and
re-parses the manifest with PyYAML at first boot, and nothing else pulls
either in) is installed in its own `apt-get` call whose failure raises
`StepError` and stops the install; the packages' own declared `apt`
(`manifest.apt`) stays a separate, deliberately lenient `check=False` call
— a package may still be usable without an optional dependency, so only a
warning is emitted. The two must never be merged back into one call: that
is exactly the "armed, advertised, and silently inert" failure class this
whole area exists to prevent.

**Kernel parameters are validated in `plan_packages`, not in `bootloader`.**
The allowlist lives in `bootloader.py::grub_defaults`, which runs at step 7 —
after `partition` has wiped the disk. `plan_packages` now calls it purely for
its validation, so `vfio-pci.ids=$(x)` is refused while the target is still
untouched. Same reason the package state file (`etc/nivuus/packages.json`) is
written **after each package**, not once at the end: a residue that describes
itself beats a partially applied install with no record.

**The wizard does not offer packages yet (2026-08-27, a named gap, not a footnote).**
`webapp/static/js/app.js` and `webapp/templates/wizard.html` call no
`/api/packages` route — packages exist in the engine and in `discover()`, but
nothing renders them for a human to pick. The only way to install one today is
a config carrying `packages: {"console": {...}}` directly (the engine path,
exercised by `test_install_engine_packages.py` and the end-to-end proof in the
console-package-hote plan's Task 7), never through the portal. Wiring the
wizard is deliberately deferred, not forgotten.

Tests: `cd installer && make test-packages`
(26 files: 9 original + 6 from the console-package phase + all 11
`test_windows_guest_*` suites, newly wired into an aggregator for the first
time since the CI runs no Python tests at all). Spec:
`docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md`.

**Build & test:** `cd installer && sudo make build-iso` (needs `live-build`).
`make test-portal` (portal on :8080), `make test-vm` (QEMU UEFI, portal via
Ethernet fallback — WiFi AP isn't emulable in QEMU), engine on a loopback image
via `--stop-after`. The riskiest path (partition/format/mount) is validated; the
debootstrap path uses standard tooling.

## Development Commands

```bash
# ── Installer ISO ──────────────────────────────────────────────────────────
cd installer && sudo make build-iso     # full live-build ISO (needs live-build)
make test-portal                        # wizard on :8080, no install
make test-vm                            # QEMU UEFI boot; portal via Ethernet fallback
                                        # (WiFi AP is not emulable in QEMU)

# install engine, staged — stops before the destructive steps
sudo python3 installer/install-engine/run.py --stop-after partition

# ── Windows guest ──────────────────────────────────────────────────────────
python3 installer/windows-guest/build.py    # unattended LTSC ISO
python3 installer/windows-guest/domain.py   # CASSE jusqu'a la phase 2c :
# ImportError des l'entree de main() (HardwareError a quitte common.hardware).
# Voir "domain.py is DEAD until phase 2c" plus haut.
python3 installer/windows-guest/retro_sync.py   # retrogaming (OPTIONAL): replay
# `retro install` with the owner's manifest, refresh the durable witness on
# D:\state\retro.status, then hold Steam and sync the library. It REFUSES to
# sync unless that witness says `ok` for the current provisioning run: `retro
# scan` builds emulator paths from the manifest without checking they exist, so
# syncing a partial install fills Steam with entries that do not start. On a
# console where retrogaming was never enabled it says so and exits 0. It also
# refuses (exit 7) while a streaming session is live - stopping Steam would cut
# the game being played, and nothing restarts it before the next Moonlight
# connection; --force overrides, loudly.

# ── Host scripts ───────────────────────────────────────────────────────────
scripts/tests/test_pcie_wifi_link_guard.sh  # 16 assertions on a fake sysfs tree
scripts/tests/test_hw_blackbox.sh           # 26 assertions
scripts/tests/test_vm_wake_gate.py
scripts/tests/test_handle_vm_start.sh       # 10 assertions on a fake virsh
scripts/disk-maintenance.sh --dry-run       # ALWAYS this first when / fills up
```

**IMPORTANT**: The project is in development mode - do NOT install packages unless explicitly required.

### Host Shell Gotchas (sessions run as root on the live server)

- The interactive zsh profile fetches the public IP at startup and ships broken `localip`/`grep`/`ip` shell functions (`FUNCNEST` errors): shell commands intermittently hang ~2 min, get killed (exit 137/143), or have their output silently eaten. **Workaround: wrap commands in `bash -c '...'`** (bash doesn't inherit the zsh functions), or read `/sys`/files directly (e.g. bridge members via `/sys/class/net/<bridge>/brif/`). **The `grep` function's precise failure mode: it does NOT prefix recursive matches with `./`** the way real `grep -r`/`grep -rn` does. Any exclusion filter written as `grep -v '^./…'` therefore matches nothing and silently passes everything through unfiltered, and any count derived from its output (`wc -l` on filtered results) comes out wrong in the same silent way — there is no error, just a clean-looking result that is not clean. This produced a false "no dead references" verification in an earlier phase and two wrong suite counts during the console-package-hote plan. **Use `command grep`, or wrap the whole pipeline in `bash -c '...'`** — the zsh function only shadows the bare `grep` invocation.
- `ot-ctl` (OTBR) output lines end with `\r` (CRLF) — strip it before string comparisons in scripts, or `case "leader"` never matches.
- **`systemctl` does NOT work from a Claude session (found 2026-08-05).** The session runs in its own **PID namespace** (`readlink /proc/self/ns/pid` ≠ `/proc/1/ns/pid`; `systemd-detect-virt` → `container-other`) while sharing the host mount namespace. systemd authenticates peers with `SO_PEERCRED`, which is meaningless across PID namespaces, so every call fails with **`Failed to connect to system scope bus via local transport: No data available`** — including `env -i` and with the sandbox disabled. **This fails silently for query subcommands** (`systemctl show -p X` just prints nothing), so a check that "returns empty" may mean *unreachable*, not *unset*. Everything else is the real host: `journalctl`, `/sys`, `/proc`, `/dev/mem`, `lspci`, and **writes to `/sys` all work**.

**WORKAROUND — drive systemd over the D-Bus system bus instead (verified 2026-08-05).** Only systemctl's *private socket* transport is broken; `dbus-daemon` authenticates by **UID**, which is namespace-independent, so `/run/dbus/system_bus_socket` works fine. Note `systemctl` cannot be coaxed onto it — as root it always prefers the private socket, and `DBUS_SYSTEM_BUS_ADDRESS` does **not** override that. Call the API directly:

```bash
M="--system --print-reply --dest=org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager"
dbus-send $M.Reload                                                    # daemon-reload
dbus-send $M.EnableUnitFiles array:string:"foo.service" boolean:false boolean:false
dbus-send $M.StartUnit  string:"foo.service" string:"replace"          # also RestartUnit/StopUnit
dbus-send $M.GetUnitFileState string:"foo.service"                     # enabled/disabled
# ActiveState/SubState: GetUnit → org.freedesktop.DBus.Properties.Get on the returned path
```

**Do NOT hand these to the user as `! systemctl …`** — the `!` prefix runs inside the same Claude session, so it hits the exact same namespace error. Only a genuine host shell (SSH/console) would work, and the D-Bus route makes that unnecessary.

### Home Assistant CLI (`/usr/local/bin/ha`)

Bash CLI for the Home Assistant REST + WebSocket API. Covers entity state management, service calls, and full CRUD on automations, scripts, and dashboards. Uses `curl` for REST, embedded Python + `aiohttp` for WebSocket (traces, dashboards). Output is colorized with `jq` formatting.

**CRITICAL**: NEVER edit `.storage/` files directly. Always use the `ha` CLI to modify Home Assistant configuration (dashboards, automations, etc.). Direct file edits can corrupt HA state or be overwritten.

**Config**: `/root/.config/nivuus/ha.conf` (`HA_URL` + `HA_TOKEN`), falls back to env vars or localhost defaults.

```bash
# Entity & state management
ha states                              # List all entity states (JSON)
ha states <entity_id>                  # Get specific entity state
ha set <entity_id> <state> [attr_json] # Set entity state + optional attributes
ha call <entity_id> <action> [data]    # Shortcut: call service on entity (auto-detects domain)
ha service <domain> <service> [data]   # Call any HA service
ha history <entity_id> [duration]      # State history (duration: Nh/Nd/Nm, default 24h)
ha log                                 # View HA error log
ha config                              # Get HA configuration
ha events <type> [data]                # Fire a custom event
ha template '<jinja2>'                 # Render a Jinja2 template
ha raw <endpoint> [method] [data]      # Raw REST API call (any endpoint)

# Automations (REST + WebSocket for traces/categories)
ha automation list                     # Table: entity_id, state (on/off), last_triggered, category, name
ha automation get <entity_id>          # Get config JSON (uses config ID from attributes)
ha automation enable <entity_id>       # Turn on
ha automation disable <entity_id>      # Turn off
ha automation trigger <entity_id>      # Trigger immediately
ha automation edit <entity_id> <file|->  # Update config from file or stdin
ha automation rename <entity_id> <name>  # Rename automation (updates alias in config)
ha automation category <entity_id>     # Get current category
ha automation category <entity_id> <name>  # Set category (auto-creates if needed, uses WebSocket entity/category registry)
ha automation icon <entity_id>         # Get current icon
ha automation icon <entity_id> <icon>  # Set icon (mdi:...), use "" to remove
ha automation create <file|->          # Create new (auto-generates timestamp config ID)
ha automation delete <entity_id> [-y]  # Delete (-y skips confirmation)
ha automation trace <entity_id>        # Execution traces via WebSocket (timestamp, state, trigger)
ha automation reload                   # Reload YAML automations

# Scripts (REST + WebSocket for traces)
ha script list                         # Table: entity_id, state (off/running), last_triggered, name
ha script get <entity_id>              # Get config JSON (slug = object_id, no attributes lookup)
ha script trigger <entity_id>          # Run script (script.turn_on)
ha script edit <entity_id> <file|->    # Update config from file or stdin
ha script create <script_id> <file|->  # Create new (user provides slug, becomes script.<slug>)
ha script delete <entity_id> [-y]      # Delete (-y skips confirmation)
ha script trace <entity_id>            # Execution traces via WebSocket
ha script reload                       # Reload YAML scripts

# Scenes (REST - same pattern as automations)
ha scene list                          # Table: entity_id, friendly_name
ha scene get <entity_id>               # Get config via attributes.id lookup
ha scene activate <entity_id>          # Activate scene (with optional --transition N)
ha scene edit <entity_id> <file|->     # Update config from file or stdin
ha scene create <file|->               # Create new (auto-generates timestamp config ID)
ha scene delete <entity_id> [-y]       # Delete (-y skips confirmation)
ha scene reload                        # Reload YAML scenes

# Blueprints (WebSocket only)
ha blueprint list [domain]             # Table: path, name, domain (default: automation)
ha blueprint get <path> [domain]       # Get blueprint details from list result
ha blueprint import <url> [domain]     # Import from URL + auto-save
ha blueprint delete <path> [domain] [-y]  # Delete (-y skips confirmation)

# Dashboards (WebSocket only - Lovelace)
ha dashboard list                      # Table: url_path, title, mode, sidebar, admin
ha dashboard get [url_path]            # Get config JSON (default dashboard if omitted)
ha dashboard set <url_path> <file|->   # Update from file or stdin
ha dashboard delete <url_path> [-y]    # Delete (-y skips confirmation)
```

**Key implementation details**:
- Entity ID prefix is auto-added: `ha script get my_script` → looks up `script.my_script`
- Automations and scenes use a config ID stored in `attributes.id` (API lookup required); scripts use the object_id directly
- `automation create` and `scene create` auto-generate a timestamp ID; `script create` requires an explicit slug
- File arguments accept `-` for stdin: `echo '{"alias":"test"}' | ha script create my_id -`
- All delete commands prompt for confirmation unless `-y` is passed
- Blueprints default to `automation` domain; pass `script` as second arg for script blueprints
- `blueprint import` fetches + auto-saves via `blueprint/save` WebSocket call

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

## Infrastructure Context

The Nivuus server runs:
- **Host OS**: Debian 13 (Trixie) — **upgraded from bookworm 2026-07-16/17** (see "Debian 13 upgrade gotchas" below). Kernel `6.12.95+deb13-amd64`.
- **Boot chain (CRITICAL, discovered 2026-07-16)**: **systemd-boot**, NOT GRUB. `/boot` (ext2, kernels from apt) is NOT what boots — kernels are copied into the ESP (`/boot/efi`, 511M, ~83% full). Two coexisting mechanisms: hand-managed BLS entries `/boot/efi/loader/entries/c8550219…-<ver>.conf` (the default, via sort-key) and **kernelstub** (Pop!_OS tool, postinst hook) which refreshes `EFI/Debian_Ginux-*/vmlinuz.efi`+`initrd.img` on every kernel install. A new kernel is NOT booted until the BLS entry points at it (this is why the machine ran 6.12.43 while 6.12.95 was installed for 11 days). kernelstub's own entries (`Debian_Ginux-current/oldkern.conf`) carry `systemd.unified_cgroup_hierarchy=false` (cgroup v1) — do not boot them on modern systemd. After any kernel install: check `bootctl list` + `file /boot/efi/EFI/Debian_Ginux-*/vmlinuz.efi`.
- **CPU**: Intel i9-12900K (8 P-cores + 8 E-cores), **undervolted**, compact case with **very limited cooling** (calibrated 2026-07-17: 50 W package ≈ 85 °C, 75 W → 100 °C TjMax; fans have no headroom — fan1 max 1769 RPM, fan2 max 2652). Thermal control since 2026-07-17 is **RAPL power capping, not a frequency cap**: `scripts/optimize-cpu-thermal.sh` (deployed at `/usr/local/bin/`, run at boot by `cpu-thermal-optimization.service`) sets **PL1=60 W/4 s + PL2=68 W** (raised from 50/58 → 65 → settled at 60 on 2026-07-23, see below) + P-cores at full 5.1 GHz turbo + E-cores 2.0 GHz. `optimize-ecores.service` also reapplies E-core settings at boot.
- **RAPL raised 50→65 then settled at 60 W (2026-07-23), validated by stress campaign + a real gaming session.** **CRITICAL sensor gotcha found doing this: `coretemp` on this box only exposes 8 DTS sensors labelled `Core 32-39` and they are NOT the loaded P-cores** — under a concentrated P-core load they read ~65-71 °C while the CPU was actually at its thermal limit. **The only trustworthy CPU temp is `PECI Agent 0` (nct6798 `temp8`) = the package Tj the CPU throttles on.** Any thermal script/monitor MUST use PECI, never a `coretemp` max, or it will silently under-read by ~25-30 °C. Measured under **concentrated gaming load (few P-cores @5.1 GHz), fans at 100%**: PL1 50 W→PECI 78-84 °C, **65 W→82-86 °C**, 80 W→89-95 °C, 95 W→99 °C (TjMax, throttling). Synthetic worst-case 65 W CPU + GPU at 199 W (stock fans auto) peaked PECI 89 °C. **Then a REAL CPU-heavy game at 65 W peaked PECI 93 °C / sustained 91-92 °C — only 2 °C under the 95 °C ceiling, too thin for heat-soak + summer ambient, so PL1 was dialed back to 60 W** (est. ~90 °C peak, ~5 °C margin; negligible in-game loss since the CPU is burst-bound — a GPU-bound game only used 13-26 W). fan2 (chassis) hit ~2560 RPM in that session — the box is fully cooling-bound under load, no fan headroom left. All-core stress is thermally much easier (heat diluted across 24 threads → PECI only ~70-77 °C at 50 W) and is NOT a valid proxy for the gaming envelope. The real limit is physical cooling (repaste/better cooler); **do not raise PL1 past 60 W without re-running a real CPU-heavy game session** (synthetic stress under-predicted the real peak by ~4 °C because it can't reproduce the in-case GPU heat + game CPU pattern together). Turbostat/RAPL package power read from `/sys/class/powercap/intel-rapl:0/energy_uj` (delta/interval).
- **Silence-first fan curve (2026-07-23, in `optimize-cpu-thermal.sh`, full-apply only so mode switches don't touch it).** Both nct6798 fans were driven by the BIOS default temp source **9 = "PECI Agent 0 Calibration"**, a bogus +20 °C-offset reading that idles at ~63 °C → both fans over-spun to ~1450/1770 RPM at idle for no thermal reason. Script now sets `pwmN_temp_sel=8` (**real PECI Tj**) + a silence curve → **idle drops to fan1 ~300 RPM / fan2 ~910 RPM** (near-silent), ramping to 100 % by 88 °C (so under gaming load PECI ~86-89 °C they still hit max automatically — silence is only an idle/light-load gain, the box stays cooling-bound under load). **fan2 (chassis) NEVER stops — point1 pwm=58 (~950 RPM):** it cools the UNMONITORED VRM/M.2/GPU-intake air, and fully stopping it (a silence-max experiment) is the **prime suspect of a hard crash on 2026-07-23** (instant power-cut, no journal/thermal/pstore trace — signature of a protection shutdown on an unmonitored rail; RAPL protects the CPU but not the VRM). fan1 (CPU header) can idle very low; fan4/6/7 headers have nothing connected. Manual PWM test (`pwmN_enable=1`): both stop at pwm≤10, min spin ~250-360 RPM at pwm20. The GPU (RTX 4070) manages its own fan (zero-RPM idle, ~40 % at 199 W/68 °C) — no host-side control (no Coolbits, would break passthrough).
- **CPU policy consolidation + VM-aware power modes (2026-07-22, two specs in `docs/superpowers/specs/`)**. Five units used to claim CPU policy; only `optimize-cpu-thermal.sh` did real work. **Removed**: `tuned`/`tuned-utils`/`tuned-utils-systemtap` purged (failed every boot since trixie — a start race lost to `power-profiles-daemon`, plus a self-defeating hand-written `/etc/systemd/system/tuned.service` override with `PrivateNetwork`/`ProtectSystem=full`), `cpufrequtils`+`loadcpufreq` purged (config was empty), `watchdog` package purged (never opened `/dev/watchdog`; the hardware watchdog is PID 1's via `RuntimeWatchdogSec=30` in `/etc/systemd/system.conf.d/10-nivuus-watchdog.conf`). **`power-profiles-daemon` is `mask`ed** (`--now`), not disabled: it is `Type=dbus` **and** `WantedBy=graphical.target`, so disabling leaves both activation paths open — masking is the only way to stop it overwriting EPP on all 24 threads every boot (the regression both specs exist to fix). `set-default multi-user.target` (GNOME startable on demand with `systemctl start gdm`; PPD masked so a hand-started session can't resurrect it). **`optimize-cpu-thermal.sh` is now mode-aware**: no arg = full apply (RAPL + fan + policy), mode auto-detected from `virsh domstate Windows` (`running`→gaming, else→idle, fallback idle); `gaming`/`idle` = policy only. **gaming** = P-cores 5100 MHz/EPP `performance` + `nivuus-cpu-latency.service` holding `/dev/cpu_dma_latency` at 250 µs (recreates today's C6 ceiling the VFIO guest needs); **idle** = P-cores 3600 MHz/EPP `power` + latency service stopped (deep C-states allowed). RAPL 50/58 W and the fan curve are **mode-agnostic**, applied on full apply only. The two libvirt CPU hooks (`10-cpu-confine.sh`/`10-cpu-release.sh`) call `systemctl start nivuus-cpu-mode@{gaming,idle}.service` — **via systemctl, never the /usr/local script directly** (the libvirtd AppArmor profile's `PUx` covers `/usr/bin/*` but not `/usr/local/*`); `nivuus-cpu-mode@.service` (`Type=oneshot`) runs the script outside the confinement. **C-state cmdline change**: `intel_idle.max_cstate=3` **removed from the default BLS entry** (`…6.12.95…conf`) — it capped idle at C6 and blocked package PC2/PC6 (the real Alder Lake idle savings) for the whole uptime to serve a condition that only holds while the VM runs (same mistake as the retired `isolcpus`); the dynamic PM QoS constraint replaces it. The 6.12.43 BLS entry keeps `max_cstate=3`+`isolcpus` as rollback. **Verified post-reboot 2026-07-23**: EPP race gone (`cpu0` EPP=`power` at boot and unchanged after 5 s, PPD masked), boot resolves to idle, cmdline free of `max_cstate` (`nohz_full=0-15` kept), `multi-user.target` default with gdm inactive, `--failed` empty, RAPL 50/58 W, hardware watchdog re-armed (`bootstatus=0`). **C8/C10 now real**: `cpu0/cpuidle` shows 5 states (C8 lat 280 µs, C10 lat 680 µs, hidden before) and their usage counters climb (`CPU%c7`≈16 % vs 0.00 with `max_cstate=3`). **Idle power measured 2026-07-23** (VM off, idle mode, host at its permanent floor — HA ~17 %, Tdarr_Server polling ~21 %, 32 containers, `Busy%`≈9.6 %): **package 8.6 W vs the 12.7 W baseline — −4.1 W / −32 %**, stable across two windows (8.62/8.61 W). `CPU%c7` 0.00 → ~35 %, and the **package now enters PC2** (`Pkg%pc2` 0.00 → 1.75-3.37 %). `Pkg%pc6` stays 0.00: the always-on services (HA/Tdarr/container heartbeats) never let all cores idle simultaneously long enough for the deepest package state — that is the always-on-server ceiling, not a limit of the change, and the per-core C7/C8/C10 savings already deliver the −32 %. The **mode-switch hooks are verified end-to-end** (real VM start→gaming/EPP performance/PM QoS 250 µs, stop→idle, no AppArmor error). The **250 µs gaming latency figure is validated**: with the states now visible (C6=220, C8=280, C10=680 µs) 250 sits between C6 and C8, correctly keeping C6 as the gaming ceiling. gotcha: **turbostat writes its table to stderr** (v2024.07.26) — capture `2>file`, a `2>/dev/null` swallows it. Both specs are now fully verified; only thermald remains as a separate follow-up. thermald remains a justified but separate follow-up (needs its own campaign; must drive `rapl_controller` only or it becomes a second frequency writer).
- **Dynamic host/VM CPU partitioning (replaced `isolcpus`, 2026-07-22)**: `isolcpus=0-15` is a *boot-time* parameter — it kept the 8 P-cores out of the scheduler for the whole uptime, so the host ran on the 8 E-cores (3.9 GHz) even with the VM shut off (measured: 6-8 kthreads per P-core vs 87-126 tasks per E-core, VM off). Now **removed from the default BLS entry** (`nohz_full=0-15` kept — tickless while the VM owns them, free otherwise) and replaced by cgroup-v2 cpuset partitioning: `scripts/vm-cpu-partition.sh` (deployed **`/etc/libvirt/hooks/vm-cpu-partition.sh`** — see the AppArmor trap below) sets `AllowedCPUs` on `system.slice`/`user.slice`/`init.scope` — **`confine`** (host → CPUs the VM does not pin) from hook `prepare/begin/10-cpu-confine.sh`, **`release`** (host → all CPUs) from `release/end/10-cpu-release.sh`; `status` shows the split. The VM lives in `machine.slice`, never touched; kthreads are outside the cgroup tree and stay global. Docker uses the **systemd** cgroup driver so its 36 containers sit under `system.slice` and are covered. The split is **derived from the VM XML** (`cputune` vcpupin+emulatorpin) minus the online set — no hardcoded core numbers; **fail-open** (unparseable XML or <4 host CPUs → cpusets untouched) and `--runtime` only, so a reboot always returns to the unrestricted state. `vm-idle-shutdown.sh` re-asserts `release` when the VM is off, because the dispatcher runs hooks in unordered `find(1)` order alongside the deadlock-prone `rebind-host-gpu.sh`. **AppArmor trap (cost a full VM-start cycle to find, 2026-07-22)**: `/etc/apparmor.d/usr.sbin.libvirtd` grants `/etc/libvirt/hooks/** rmix` — `ix` means hooks run *inheriting the libvirtd profile*, which allows exec of `/bin/*`, `/sbin/*`, `/usr/bin/*`, `/usr/sbin/*` (all `PUx`) **but not `/usr/local/sbin/*`**. A hook calling a script there dies with the misleading `/bin/bash: bad interpreter: Permission denied` and **no AppArmor DENIED line in dmesg**. Anything a libvirt hook executes must live under `/etc/libvirt/hooks/` or one of the `PUx` dirs. Corollary: the hook wrappers `exit 0` so they can never block a VM start, which also *masks* such failures — the dispatcher logs `code 0`; the real error only surfaces in `/var/log/libvirt-cpu-hook.log`, so check that file, not the journal, when a hook silently does nothing. Also removed `systemd.unified_cgroup_hierarchy=false` from the kernelstub config (cgroup v1 would break this). The 6.12.43 BLS entry deliberately **keeps `isolcpus`** as a rollback path.
- **GPU**: NVIDIA RTX 4070 (VFIO passthrough). PCIe link capped at **Gen3 x16** by the platform (ASUS ROG STRIX B660-G: root port `00:01.0` LnkCap2 max 8GT/s while the card supports 16GT/s; measured ~12 GB/s host→device in-VM, Gen4 would give ~25 GB/s). Check BIOS PCIEX16 Link Speed (Auto/Gen4) to lift it. Idle `LnkSta 2.5GT/s (downgraded)` is normal power management.
- **GPU ownership: host by default, VM on demand (changed 2026-07-22)**: `/etc/modprobe.d/vfio.conf` used to list the RTX 4070 in `options vfio-pci ids=10de:2786,10de:22bc,144d:a808`, which — combined with `softdep nvme pre: vfio-pci` — captured the card from the **initramfs** onwards. A cold boot therefore left the host with no NVIDIA driver at all: `systemd-modules-load` failed (`nvidia-drm` → `could not insert nvidia_current: No such device`), `nvidia-persistenced` failed behind it, and `nivuus-ollama` exited `rc=128` (`nvml error: driver not loaded`) until a VM start/stop cycle handed the card back. The `ids=` list is now **`144d:a808` only** (the Samsung NVMe, passed to the same VM but never touched by the hooks — it must stay statically bound). The GPU is owned by the host at boot (`nouveau` is blacklisted twice, so `nvidia` wins) and moved on demand by the already-existing hooks: `bind-vfio-gpu.sh` detaches it at VM start, `rebind-host-gpu.sh` returns it at VM stop. **This requires regenerating the initramfs AND getting it into the ESP** — `update-initramfs` triggers kernelstub, which does the ESP copy and rewrites its own entries (verify with `unmkinitramfs` + `bootctl list`). ESP is chronically tight (511M): `initrd.img-previous` was moved to `/media/backup/esp-20260722/` to make room; kernelstub recreated it. **The display stack must never see the card (CRITICAL)**: the host boots to `graphical.target` with **gdm running GNOME/Wayland on the Intel iGPU** (`00:02.0`, single DRM node `card0`). If the NVIDIA card exposes a DRM node, mutter/gdm opens and holds it, and `bind-vfio-gpu.sh`'s `modprobe -r nvidia` then fails at VM start — the VM would boot without its GPU. So the host loads **`nvidia` alone, never `nvidia-drm`**: `/etc/modules-load.d/nivuus-nvidia.conf` lists `nvidia` (it has **no PCI modalias**, so nothing autoloads it), and the alternatives-managed `nvidia-drm` entry is neutralised in `/etc/nvidia/nvidia-load.conf` — deleting the `/etc/modules-load.d/nvidia.conf` symlink is useless, any `update-alternatives` run on the `glx` group recreates it (verified). This mirrors the state `rebind-host-gpu.sh` already produces (`modprobe nvidia` only), in which ollama works. Also removed: `/etc/X11/xorg.conf.d/99-nvidia-headless.conf` (local file, 2025-05-02, no package, no other `Coolbits` consumer) which pinned Xorg to `BusID "PCI:1:0:0"` — a landmine now that the card must stay releasable; backed up to `/media/backup/xorg-20260722/`. **Trade-off**: VM start now depends on a successful detach from nvidia (the bind hook already stops persistenced/ollama/tdarr and waits for `/dev/nvidia*` holders — a production-proven path, since it already ran on every VM start following a VM stop). **Installer divergence**: `install.sh` still puts `vfio-pci.ids=<gpu>` on the kernel cmdline, because it does **not** deploy the GPU bind/rebind hooks — a fresh install has no other way to make passthrough work. Aligning it means shipping those hooks first.
- **VM start / GPU rebind — six traps found and fixed 2026-07-22 (evening)**. The VM could not start *at all* and CUDA died after every gaming session; both are fixed, and all causes are invisible from their symptoms.
  1. **NEVER call `virsh` from a libvirt hook.** Hooks run while libvirtd holds the domain job lock and waits for them, so any virsh call re-enters libvirtd and deadlocks it: `virsh start Windows` never returns, virsh clients pile up, hook processes survive a `systemctl restart libvirtd`. `vm-cpu-partition.sh` did `virsh dumpxml` to derive the cpuset split and `bind-vfio-gpu.sh` did `virsh nodedev-detach`. Fixes: the partition script now reads the **domain XML from stdin**, which libvirt already feeds to qemu hooks and the dispatcher passes through untouched (`"$file" "$@"`, it never consumes stdin), falling back to virsh only outside hook context (`status`, `vm-idle-shutdown.sh`); the bind hook's detach calls were **deleted outright** — the `<hostdev>` entries are `managed='yes'`, so libvirt binds vfio-pci itself. Recovery when it happens: `pkill -x virsh; systemctl restart libvirtd`. Note `10-cpu-confine.sh` sorts before `bind-vfio-gpu.sh` in `find(1)` order, so a hang there means the GPU hook never runs and its log stays stale — check mtimes before believing a hook log.
  2. **CUDA 999 after every VM cycle = a stale CDI spec, not the driver.** `nvidia_uvm` gets a **dynamically allocated** char-device major; the bind hook must unload it, and the reload can change it (seen 510 → 511). `/var/run/cdi/nvidia.yaml` is a frozen snapshot generated once at boot, so every `--gpus` container then got `/dev/nvidia-uvm` with the wrong major — the node opened fine but was not the UVM driver, and all CUDA entry points returned 999 while `nvidia-smi` kept working (NVML only uses `/dev/nvidiactl`, major 195, static). That is why only a reboot fixed it (`/var/run` is tmpfs) and why reloading modules made it *worse*. **Fix: `nvidia-ctk cdi generate --output=/var/run/cdi/nvidia.yaml` in the rebind hook**, after the driver loads, before the containers. Discriminating test that cracked it: `--gpus all` fails while `--privileged -v /dev:/dev` works ⇒ container layer, not driver.
  3. **The AppArmor trap is wider than `/usr/local/*`.** AppArmor resolves symlinks *before* matching, so `/usr/bin/* PUx` does not cover `/usr/bin/nvidia-smi` → `/etc/alternatives/…` → `/usr/lib/nvidia/current/nvidia-smi`. **Any `update-alternatives`-managed binary is unusable from a hook**, failing with `Permission denied` and no DENIED line in dmesg. The rebind hook now reads `/proc/driver/nvidia/gpus/*/information` instead (no exec). `nvidia-ctk` is a real `/usr/bin` path and is fine.
  4. **`nvidia-persistenced` needs a longer start deadline after passthrough.** The packaged unit is `Type=forking` with `TimeoutStartSec=5s`; on a card just back from VFIO, GPU init exceeds it and systemd kills a perfectly healthy daemon (`start operation timed out`). Drop-in `/etc/systemd/system/nvidia-persistenced.service.d/10-nivuus-start-timeout.conf` raises it to 60s. Without it the socket is missing and `nvidia-container-toolkit` refuses to create any `--gpus` container (`exit 127 … /run/nvidia-persistenced/socket`).
  5. **A slow hook is indistinguishable from a deadlock — and this one was very slow.** `bind-vfio-gpu.sh`'s `gpu_holders()` scanned every `/proc/[0-9]*/fd/*` in pure bash, forking a `readlink` per descriptor. On this host under load (965 processes, ~16000 fds) **one scan took 40 s**, and it is called up to 15 times, so `virsh start` could sit silently for 10 minutes. Hours were lost diagnosing phantom hangs, and worse: a start "killed" as stuck actually completed later in the background, leaving libvirt convinced the GPU was still assigned to the domain (`nodedev-reattach` then fails with "utilisé par le pilote QEMU") — recoverable only with `systemctl restart libvirtd`. Now `find /proc/[0-9]*/fd -lname '/dev/nvidia*'`: **226 ms**. Rule of thumb: before concluding a hook has deadlocked, time it, and check whether `/run/libvirt/qemu/<domain>.pid` exists.
  Also: `rebind-host-gpu.sh` had **no logging at all** (its twin redirects to a log) — added, and it immediately exposed traps 3 and 4. **Any process holding `/dev/nvidia*` blocks the VM start**, and the bind hook only knows how to stop the containers it names — a stray user-session tool (here `mcp-memory-service`) will silently break passthrough; the hook now refuses the start instead of proceeding, which required patching the dispatcher `/etc/libvirt/hooks/qemu` to propagate non-zero exits **for `prepare` hooks only** (it used to `exit 0` unconditionally, so no hook could ever refuse anything).
  6. **Refusing a start runs the `release` hooks — which re-triggered the virsh-in-hook deadlock from the *rebind* side (found 2026-07-22, exercising the refusal path end-to-end for the first time).** `rebind-host-gpu.sh` still did `virsh nodedev-reattach pci_0000_01_00_{0,1}`. On a *normal* stop that runs without contention, so it never bit. But when a `prepare` hook **refuses** a start, libvirt runs the `release`/`stopped` hooks to unwind the aborted start **while still holding the domain job lock** — the reattach then re-enters libvirtd and wedges it, exactly like trap 1 but on the release side. Fix: the two `virsh nodedev-reattach` calls were **deleted** — the hostdevs are `managed='yes'`, so libvirt reattaches them itself (01:00.0→nvidia, 01:00.1→snd_hda_intel, 03:00.0→vfio-pci) during teardown, before the release hook runs; the hook now just **waits on sysfs** (`/sys/bus/pci/devices/0000:01:00.0/driver` == nvidia) before touching modules/persistenced, no libvirtd call. Symmetric to the bind-side detach removal. **The refusal path is now validated end-to-end**: a synthetic holder of `/dev/nvidia0` → `virsh start` returns rc=1 with a clear "Hook script … prepare begin … exit status 1", the host is restored, and **libvirtd stays healthy** (a normal VM cycle right after still rebinds the GPU to nvidia in ~1 s with CUDA working, proving the virsh removal caused no regression). **Recovery if a hook ever wedges libvirtd again — WITHOUT a reboot**: the stuck libvirtd process becomes a zombie whose one running thread **spins at 100 % in the kernel** (`stime` climbing, `utime` frozen), so a pending `SIGKILL` is never delivered and `kill -9` does nothing. That thread holds an flock on `/run/libvirtd.pid` **and every `/run/libvirt/*/driver.pid`** (network, qemu, storage, nodedev, …) — the second is the real blocker (`Impossible d'acquérir … network/driver.pid`). flocks bind the *inode*, not the path, so `mv` each `*.pid` aside, `systemctl reset-failed libvirtd.service libvirtd*.socket`, then restart the `.socket` units and trigger with `virsh list`: the fresh libvirtd creates new inodes and takes over. The zombie thread eventually returns from its kernel section on its own and gets reaped (~8 min here) — the burned core comes back without a reboot.
- **Hypervisor**: QEMU/KVM with libvirt
- **Network**: 3 bridges (localBridge, publicBridge, internalBridge), PPPoE connection
- **Firewall**: firewalld with multiple zones
- **WiFi**: Dual-band hostapd (2.4GHz + 5GHz)
- **Docker**: ~33 containers; the HA stack (Home Assistant, Mosquitto, zigbee2mqtt, OTBR, …) is defined in `/opt/nivuus/HomeAssistant/docker-compose.yml` (zigbee2mqtt replaced ZHA on 2026-07-14)
- **monitor_docker (HA custom component)**: locally patched (v1.20b3, `helpers.py`) so the Docker-events loop respects the `containers:` include list — otherwise ephemeral `agent2-e2e-runner-*` containers spam errors when they vanish mid-setup. Re-apply the guard in `_container_add`/`_container_remove` if the component gets updated.
- **systemd ordering-cycle trap (fixed 2026-07-16)**: `kinect-usb-setup.service` had `After=multi-user.target` + `Before=docker.service`, forming a cycle with docker's `WantedBy=multi-user.target`. systemd breaks cycles by deleting an *arbitrary* start job — on the 2026-07-16 reboot it deleted `docker.service` (and `selfmod-watchdog.service`), so Docker (34 containers incl. HA) never started at boot with no failure trace. Fix: removed the `After=multi-user.target` line. If an enabled service is silently absent after boot: `journalctl -b | grep "ordering cycle"`. Never combine `After=multi-user.target` with `Before=<service wanted by multi-user.target>`.

### Debian 13 (Trixie) upgrade gotchas (upgraded 2026-07-16/17)

The bookworm→trixie dist-upgrade broke several things; all fixed, documented here so they don't bite again:
- **SSH down after boot (`/run/utmp`)**: trixie stopped auto-creating `/run/utmp`; `ssh.service`'s systemd hardening (namespaces) fails without it → port 22 refuses ALL connections while everything else (WiFi/web) works. Fix in place: `/etc/tmpfiles.d/utmp.conf` → `f /run/utmp 0664 root utmp -`.
- **VM won't start — virtiofsd moved**: trixie split `virtiofsd` out of `qemu` (Rust rewrite). Old path `/usr/lib/qemu/virtiofsd` gone → `/usr/libexec/virtiofsd` (install `virtiofsd` package). `Windows.xml` `<filesystem>` binary path updated (shares `/media/data` → tag `Data`).
- **VM won't start — OVMF 2M→4M**: trixie ships only 4M OVMF. Updated `Windows.xml` loader → `/usr/share/OVMF/OVMF_CODE_4M.fd`, nvram template → `OVMF_VARS_4M.fd`. The old 2M varstore (131072 B) is incompatible with 4M CODE → had to regenerate it (old one backed up to `/media/backup/pre-trixie-20260716/`); Windows re-registered its boot entry and booted fine (192.168.3.2 reachable). Always edit VM XML via `virsh define`, never the file directly.
- **GPU-rebind hook deadlock**: `/etc/libvirt/hooks/qemu.d/Windows/release/end/rebind-host-gpu.sh` calls `virsh nodedev-reattach` back into libvirtd — on VM `destroy` during the degraded post-upgrade state this deadlocked and piled up ~120 stuck virsh (incl. the agent's VmManager polls). Recovery: kill the hook chain + `systemctl restart libvirtd`. Latent fragility — watch on next VM shutdown.
- **VM machine type upgraded 7.2→9.2 (2026-07-17)**: `Windows.xml` was `pc-q35-7.2`; bumped to `pc-q35-9.2` (max on Trixie's QEMU 10.0 / libvirt 11.3). Windows re-booted fine on the new chipset, GPU/VFIO passthrough intact (192.168.3.2 reachable, gaming/RDP/WinRM ports up). Always via `virsh define`. Pre-change XML backed up to `/media/backup/pre-trixie-20260716/Windows.xml.pre-machine-upgrade-*`.
- **VM ACPI shutdown — root cause found & FIXED (2026-07-17)**: Windows "ignoring ACPI shutdown" was NOT a Windows power-config issue (PBUTTONACTION=3/shutdown, Fast Startup off, "ACPI Fixed Feature Button" device healthy). The real cause: on **libvirt 11.x, plain `virsh shutdown` (default mode) does not deliver the ACPI power-button event** to a guest without a qemu-guest-agent — it reports "in progress" then nothing. `virsh shutdown --mode acpi` (= QMP `system_powerdown`) works reliably (verified: guest powers off in ~25s). Fixed in code: `VmManager.ts` stop/restart now pass `--mode acpi`. **Interactive rule: always `virsh shutdown --mode acpi Windows`** (plain shutdown silently no-ops; `virsh destroy` is the forceful fallback). This VM has **no guest-agent channel** — adding one (virtio-serial `org.qemu.guest_agent.0` + qemu-ga in Windows) would enable `--mode agent` and clean OS shutdowns; not done yet. Run PowerShell/cmd in the VM via **`winvm "<cmd>"`** (`/usr/local/bin/winvm`, WinRM to 192.168.3.2 as Administrateur; note: admin password is hardcoded in that script). `winrm_exec.js` is currently broken (its global `nodejs-winrm` module was lost in the Node 24 upgrade).
- **mqtt-system-agent MQTT credentials (2026-07-17)**: the broker password is NOT in the repo/deployed `config/agent.yaml` (ships `CHANGE_ME_MQTT_PASSWORD`, and a `.deb` install overwrites `/opt/.../config/agent.yaml`). Real creds live in drop-in `/etc/systemd/system/mqtt-system-agent.service.d/mqtt-credentials.conf` (`Environment=MQTT_USERNAME/MQTT_PASSWORD`, mode 600) — `config.ts` applies these as overrides, so they survive package upgrades. **After any `dpkg -i` of the agent, the config placeholder is back but the env drop-in still wins.** `fpm` was repaired (gem reinstalled under ruby3.3; its shebang pointed at the gone ruby3.1), so `npm run package:deb` works again. the **Plex** apt repo (SHA1 signing key) fails → left disabled (`plexmediaserver.list.disabled-sha1key`). Harmless: **Plex actually runs as the Docker container `mediamanager-plex-1` (`linuxserver/plex`)**, not the apt package (a redundant leftover). `cuda-debian11` repo also left disabled (debian11/obsolete). **nvidia decision RESOLVED 2026-07-17**: host driver reinstalled from Debian non-free (`nvidia-driver` 550.163.01, DKMS built for 6.12.95+deb13; leftover cuda-repo 560.35.05 packages — nvidia-alternative, libnvidia-ml1, firmware-nvidia-gsp, nvidia-vdpau-driver — downgraded to Debian 550 with `--allow-downgrades`). GPU is now actually usable by the host (ollama/CUDA) whenever the VM is hibernated: the existing libvirt hooks do the vfio↔nvidia rebind dance around VM start/stop (bind hook stops nvidia-persistenced+ollama then `modprobe -r nvidia`; release hook reattaches + `modprobe nvidia` + restarts ollama). `docker.list` moved bookworm→trixie; nvidia-container-toolkit/wazuh/chrome re-enabled fine.
- **fail2ban**: was a broken pip venv (Python 3.13). Now the Debian package (1.1.0-8) — an `apt-listbugs` pin (`-30000`, bookworm-era, rsyslog now present) blocked it; pin removed. 8 jails load. NB: leftover pip `/usr/local/bin/fail2ban-*` still shadow PATH (service uses `/usr/bin` explicitly — fine, but confusing interactively). **GOTCHA (2026-07-27): `fail2ban-client reload` does NOT re-instantiate a jail's `banaction` when you *change* it (e.g. `nftables-multiport`→`nftables-allports`) — the jail then runs with `No actions for jail …` (detects but CANNOT ban). A full `systemctl restart fail2ban` is required after any `banaction`/`port` change. Verify with `fail2ban-client get <jail> actions`.**
- **SMB (445/tcp) kept open on the WAN but hardened (2026-07-27, user chose "durci" over VPN/allowlist).** The host `smbd` (`0.0.0.0:445`) is reachable from `ppp0`; `external` zone has `ports: 445/tcp`. Samba config was already strong (`server min protocol = SMB3`, `server signing = required`, `server smb encrypt = required`, `map to guest = Bad User`, `restrict anonymous = 2`, `smb ports = 445` so **NetBIOS 139 is off**; **one account only, `mallanic`**, every share `valid users = mallanic`) — so smb.conf was left untouched. Two layers added: (1) **fail2ban more aggressive** — `samba-auth` maxretry 5→3, and both `samba-auth`+`samba-scan` switched `nftables-multiport`→**`nftables-allports`** (ban the source on ALL ports; safe because LAN is in `ignoreip` and mallanic is the sole account, so any WAN auth-failure is an attacker by definition) with the existing progressive escalation (48h→4w, `bantime.increment`+`overalljails`). (2) **Per-source connection rate-limit** in a dedicated nft table `inet nivuus_smb` (`/etc/nftables.d/nivuus-smb-ratelimit.nft`, loaded by `nivuus-smb-ratelimit.service`, enabled): `iifname "ppp0" tcp dport 445 ct state new` → per-`saddr` token bucket 10/min burst 15 → silent `counter drop` (no log, to avoid EventMonitor amplification; inspect via `nft list table inet nivuus_smb`). Hooked at `priority filter - 5` (before firewalld's input +10). **It survives `firewall-cmd --reload` for the same reason `f2b-table`/`crowdsec` tables do — firewalld's nft backend only rebuilds `table inet firewalld`, never foreign tables** (verified; the mqtt FirewallManager triggers reloads); a full `systemctl restart firewalld` is covered by the unit's `PartOf=firewalld.service`+`After=`. The stronger options remain available if ever wanted: WireGuard (SMB→LAN-only, 445 off the WAN) or a firewalld source-IP allowlist.
- **`libvirt-daemon-system-systemd`** was marked `apt-mark manual` to stop `autoremove` purging it (trixie modular-daemon transition wanted to drop it while the monolithic `libvirtd` is what runs the VM).
- **clamav-clamonacc (on-access scanning) — FIXED 2026-07-17.** Three separate causes, not systemd sandboxing: (1) `OnAccessIncludePath /var/lib/docker/volumes` made clamonacc recursively inotify-watch the build-agent volumes (huge `node_modules` trees) → exhausted `fs.inotify.max_user_watches` (65536) → clamonacc exited at startup ("No space left on device"). Fix: removed that include (kept `/media/data/Downloads`), raised the limit to 524288 in `/etc/sysctl.d/99-inotify.conf`. (2) fd-passing scans all failed with "Not a regular file" because the clamd AppArmor profile (`/etc/apparmor.d/usr.sbin.clamd`) lacked `flags=(attach_disconnected)` — added it (+ `/media/data/Downloads/** r`), profile stays **enforce**. (3) clamd runs as user `clamav` ≠ root, so without `--fdpass` it re-opened files by path and hit "Access denied". Fix: drop-in `/etc/systemd/system/clamav-clamonacc.service.d/override.conf` adds `--fdpass`. Verified end-to-end with EICAR (detected + moved to `/root/quarantine`). NB `--move` logs a benign "Invalid cross-device link" (rename across /media→/root FS) then falls back to copy+delete.
- **Claude sessions during big system ops**: `/tmp` is a tmpfs (fstab) that can get re-mounted (stacked) mid-upgrade, killing Claude's tool exec (it holds the old `/tmp` view). Workaround used: `export TMPDIR=~/.tmp-claude` before `claude --resume`. **Symptom seen 2026-08-05: every Bash call dies with `ENOENT: no such file or directory, mkdir '/tmp/user/0/claude-0/<project>/<session>/tasks'`, and `Write` to that path fails too** (the session's stale mount view is unwritable) — i.e. Bash is gone for the rest of the session, unrecoverable in-session. **Read still works**, so a system's state can still be established from `/proc` and `/sys`: unit liveness via `/sys/fs/cgroup/system.slice/<unit>/cgroup.procs` + `/proc/<pid>/{comm,cgroup}`, listening sockets via `/proc/net/tcp{,6}` (port in hex, `st` `0A` = LISTEN — e.g. `:0016` = 22), and **socket inode numbers date a listener** (boot-era inodes are small; a much larger one proves a recent re-bind). `/proc/<pid>/stat` is NOT readable through the Read tool ("would block or produce infinite output").

### Energy/perf pass 2026-07-17 (afternoon)

- **Hugepages pool halved**: was 16584 (double the VM's need, ~16 GB wasted while host swapped); now `vm.nr_hugepages = 8448` in `/etc/sysctl.d/50-virsh.conf` (VM uses 8205, margin 243).
- **VM vCPU topology fixed to 7 cores × 2 threads** (was 14×1 — Windows didn't know about HT siblings, scheduled two heavy threads on one physical core). Applied via `virsh define`; **takes effect at next VM restart**. Backup: `/media/backup/Windows.xml.pre-topology-*`.
- **VM auto-hibernate when idle**: `vm-idle-shutdown.timer` (10 min) → `/usr/local/sbin/vm-idle-shutdown.sh`: no Sunshine/RDP conntrack flows AND VM CPU <50% of one core for 3 consecutive checks → **hibernate** via `winvm "shutdown /h /f"` (session/games preserved; WinRM call times out while guest sleeps — exit code is meaningless, the script watches domstate; ACPI-shutdown fallback), then re-arms both wake sockets (also self-heals them whenever VM is off). VM `autostart` was **disabled** (wake-on-demand starts it). NB `virsh` output is localized — scripts must use `LC_ALL=C`.
- **VM hibernation (S4) enabled 2026-07-17**: `<suspend-to-disk enabled='yes'/>` + **firmware auto-selection removed** (`<os firmware='efi'>` → explicit `<loader>/<nvram>` config) because Debian's OVMF firmware descriptors (`/usr/share/qemu/firmware/*.json`) only declare `acpi-s3`, so libvirt refuses S4 with auto-selection ("Unable to find 'efi' firmware") even though OVMF handles S4 fine. `powercfg /hibernate on` done in guest. Measured: hibernate 5-6 s; resume 5-45 s with session intact and GPU driver re-initialized OK. **Lock screen at resume fixed** via policy reg `HKLM\SOFTWARE\Policies\Microsoft\Power\PowerSettings\0e796bdb-100d-47d6-a2d5-f7d2daa51f51` (AC+DC SettingIndex=0, "require password on wake" off) + `NoLockScreen=1` (Personalization policy) — the powercfg `CONSOLELOCK` alias does not exist on Server 2022. **UX contract: Moonlight open on the host grid = its status polls keep waking the VM** (that's the wanted open-app-wakes-VM feature) — close Moonlight and the VM hibernates 30 min later. **SudoMaker Virtual Display Adapter (SudoVDA) — RE-ENABLED 2026-07-23 to fix ultrawide streaming.** It was disabled 2026-07-17 (ProblemCode 22 = `CM_PROB_DISABLED`, *not* a broken driver) and streaming fell back to the physical HDMI dummy plug. See "Cloud-gaming host = Apollo + SudoVDA" below. Sunshine/Apollo struggles to capture the secure desktop (lock screen) and drops the stream after ~10 s — moot now that resumes land on an unlocked desktop.
- **MQTT agent syslog feedback flood (major)**: Windows guest polls RAPL MSRs → with `kvm.ignore_msrs=1` the kernel logged each ignored rdmsr (0x601/0x615/0x64b) → EventMonitor amplified ~125 err/min into **~480 MQTT msg/s** (republish loop) → HA recorder at 95% CPU, RSS ballooned to 12 GB in 3 h, DB 9.1 GB. Fixes: `kvm.report_ignored_msrs=0` (runtime + `/etc/modprobe.d/kvm-quiet.conf`), agent+HA restarted. **EventMonitor's ×240 amplification bug is NOT yet fixed in code** (`nivuus/mqtt:src/features/events/EventMonitor.ts` — likely re-reads journal without cursor). Also: 30 GB stale recorder DB backup at `/opt/nivuus/HomeAssistant/config/home-assistant_v2.db.backup-20251003-*`, and the flooding entity name has a double prefix (`linux_system_agent_linux_system_agent_…` — naming bug).
- Windows power plan switched Économie d'énergie → **Performances élevées** (gaming VM).

### Disk space: audit + permanent bounds (2026-07-27)

`/` (SSD 914 G) had reached **78 % (669 G)**; brought back to **49 % (419 G)** — **250 G freed**, no service interrupted. What matters is *why* it filled, since every offender was an **unbounded cache with no GC**:

- **`/root/.cache/uv` = 54 G of `.tmp*` garbage.** uv leaves a ~130 MB temp dir behind **every interrupted operation** and never reclaims it: **448 of them** had piled up over four months (the real cache, `archive-v0`, is only 7 G). This is the single biggest recurring leak on this host.
- **Cline (VS Code `saoudrizwan.claude-dev`) = 42 G**: one shadow **git repo per task**, unbounded — a single `.git/objects/pack` had reached **38,8 G** (dated 2025-07-20). Its `tasks/` dir (conversation history, 8,5 M) is separate and was preserved.
- **A second, dead Docker daemon**: rootless dockerd under `~mallanic/.local/share/docker` = **37 G**, service `disabled`+`inactive`, 0 container, last write April 2026. Easy to miss — `docker system df` only ever shows the root daemon.
- **HA recorder DB = 11 G for 0,55 G of real data (94,6 % free pages).** `purge_keep_days: 5` deletes rows but **SQLite never returns the pages** without a repack — a leftover of the MQTT flood incident. Fixed via `recorder.purge` + `repack: true` → **562 M**. `statistics` (3 260 956 rows = long-term history) is untouched by both purge and repack, verified identical before/after. **Never `sqlite3 VACUUM` the file while HA runs**; and a manual `.backup` on this live DB **never converges** (it restarts on each concurrent write) — rely on HA's own nightly backup, which does include the DB (`"exclude_database": false`, `"protected": true` so its inner tar isn't listable).
- **Google Takeout stored twice** in Agent2: 46,5 G of ZIP **plus** their 28 G extraction. Moved the ZIPs to `/media/backup/agent2-takeout/` (with `media-db-mapping.txt`, since `media.db` references them by in-container path `/home/agent/personas-studio/data/...`); some are **split archives** (`.z01`/`.z02`) — never delete partially. A `recovered/` subdir held **10 G of byte-identical duplicates** of the same ZIPs, referenced nowhere.
- Also: 20 G of ollama models unused for 15-16 months (`qwen3:14b` is the only one wired into HA), 13 G of `.git/lfs` objects duplicating the checked-out `safetensors` in `Projects/OpenVINO2/models` (sources kept in `MODEL-SOURCES.txt`), 6,2 G apt archives, 5,7 G of 2023 ISOs sitting in `libvirt/qemu/snapshot/` **with no snapshot defined**, a 3,2 G core dump.

**Permanent bounds now in place** (this is what stops the regrowth):
- `scripts/disk-maintenance.sh` → `/usr/local/sbin/nivuus-disk-maintenance.sh`, run weekly by `nivuus-disk-maintenance.timer` (Sun 04:30, `IOSchedulingClass=idle`). Bounds uv temps (>2 d), apt cache (>1 G), Cline checkpoints (>30 d), pip/npm caches (>5 G), Docker build cache (>25 G), pacct logs (>14 d). **`--dry-run` supported** — always the first thing to run when `/` fills again.
- `/etc/docker/daemon.json` gained `builder.gc` (20 GB ceiling) + per-container log caps (`max-size: 50m`, `max-file: 3`); validated with `dockerd --validate`. **Takes effect only at the next docker daemon restart** — the weekly script enforces the ceiling meanwhile.
- `/etc/apt/apt.conf.d/99nivuus-cache-limits`: `CleanInterval "14"` — `AutocleanInterval` alone only drops *undownloadable* packages, which is why 2 779 `.deb` had survived.
- HA automations: `Maintenance - Repack mensuel base recorder` (1st of month 04:15) and `Alerte - Disque systeme sature` (>85 % for 30 min → persistent notification). NB **HA no longer exposes persistent notifications as entities** (WebSocket only since 2023) — don't look for them in `states`.
- Untouched by choice: Docker build cache (66 G, 37 G reclaimable), dangling images (20 G), orphan volumes (11 G incl. 6,9 G of `act-*` GitHub-Actions-local). The weekly GC will trim the build cache to 25 G.
- Found in passing: the automation `Notifications - Relai Bleuenn…` is **broken** (`rest_command` → `localhost:8080` refused), so persistent notifications currently relay nowhere.

### Security follow-ups audited 2026-08-05 (two of four claims were wrong)

- **Docker runs with `"iptables": false` (`/etc/docker/daemon.json`) — this is the single fact that governs container port exposure.** Docker creates **no** nat/DNAT rules (`nft list table ip nat` has no DOCKER chain), so a published port is reachable only through the userspace `docker-proxy`, which holds a host socket. Traffic therefore hits the host **INPUT** chain and **firewalld applies normally** — the usual "Docker bypasses firewalld" hole does not exist here. Consequence for auditing: to know whether a published container port is WAN-reachable, read `firewall-cmd --zone=external --list-all`, not the Docker port bindings. Mosquitto publishes 1883/1884/8883/8884 on `0.0.0.0` but none are in the `external` zone → LAN only.
- **MQTT default credentials: claim is FALSE.** `mqtt` / `CHANGE_ME_MQTT_PASSWORD` is rejected (`Connection Refused: not authorised`). The broker's `password_file` is `/mosquitto/data/passwd` (**not** `/mosquitto/config/passwd` — an audit that greps the wrong path concludes "no passwd file"), holds one user (`mqtt`) with a real 12-char password matching the systemd drop-in. The placeholder only ever lived in the repo's `config/agent.yaml`.
- **Real MQTT exposure is listener 8883**, `allow_anonymous true` (TLS, Meross bulbs authenticate with their MAC, which cannot go in a mosquitto password_file). It is constrained by `acl_meross` (`pattern readwrite /appliance/#` + `/app/#`) so zigbee2mqtt/homeassistant topics stay unreachable from it, and by the `external` zone which does not open 8883. Documented, deliberate trade-off.
- **tlog tmpfiles bug: the package is NOT at fault, do not report upstream.** The packaged `/usr/lib/tmpfiles.d/tlog.conf` is correct (`d /run/tlog 0755 _tlog _tlog`, and both the `_tlog` user uid 118 and group gid 129 exist). The failure came from three **local, package-less** artifacts: `/etc/tmpfiles.d/tlog.conf` (which *shadows* the packaged file — same basename, `/etc` wins — and requested mode **0777** with group `tlog`, hence `Failed to resolve group 'tlog': No such process` at boot), `/etc/init.d/create-tlog-dir` (SysV, `chmod 777 /run/tlog`, spamming `systemd-sysv-generator` warnings on every daemon-reload), and the `zz-tlog-fix.conf` workaround. **All three removed 2026-08-05** (backup `/media/backup/tlog-cleanup-20260805/`); `/run/tlog` is now `_tlog:_tlog 0755` and `systemd-tmpfiles --create` is clean. `tlog` was **wired into nothing** — no sshd `ForceCommand`, no PAM (a `grep tlog /etc/pam.d/` match is the false positive `pam_las**tlog**.so`), no unit, no shell in `/etc/passwd` — so `tlog`+`libtlog0` were **purged**, along with the residual `_tlog` user/group and a stray **self-bind-mount of `/run/tlog` onto itself** (another `tlfix` leftover, holding a `state` file; `rmdir` fails with EBUSY until it is `umount`ed). ⚠️ The purge armed a latent SSH landmine — see the `ReadWritePaths` entry below.
- **Purging a package can arm a `ReadWritePaths` landmine in an unrelated unit — check before removing anything (2026-08-05).** `/etc/systemd/system/sshd.service.d/harden.conf` (hand-made local file, 29 mars 2025, **owned by no package and not generated by `install.sh`/the installer**) carried `ReadWritePaths=/var/run/tlog /var/spool/exim4 /var/run/utmp`. A **missing** `ReadWritePaths` entry is fatal: sshd dies at `Failed to set up mount namespacing: /./run/tlog: No such file or directory` → `226/NAMESPACE`, which is exactly how ssh failed at the 07:45 boot. Purging tlog therefore re-armed the original bug **permanently** — the running sshd only survived because it predated the removal, and the next `restart`/reboot would have killed SSH for good. Two traps compound it: the drop-in lives in **`sshd.service.d/`** while the Debian unit is **`ssh.service`** (systemd applies it via the `Alias=sshd.service`, so it is easy to miss with `ls /etc/systemd/system/ssh.service.d/`), and `systemctl` is unusable from a Claude session, so the effective value must be read over D-Bus: `Properties.Get org.freedesktop.systemd1.Service ReadWritePaths` on the object from `GetUnit string:"ssh.service"`. Fixed by dropping `/var/run/tlog` from the line (the other two paths were verified to exist first). **Rule: before `apt purge`, grep `/etc/systemd/system/*/*.conf` for the paths the package owns; and prefix optional paths with `-` (`-/var/run/tlog`) so a missing dir degrades instead of killing the unit.**
- **Host-root surface: the socket-proxy is not the weak link — the HA container itself is.** `homeassistant` runs `privileged: true` + `network_mode: host` + `user: "0:0"` + `/dev:/dev`, which is already unrestricted host root by construction; hardening the Docker API changes nothing while that holds. The `docker-socket-proxy` (`tecnativa`, bound `127.0.0.1:2375` only) is in fact reasonably tight: `EXEC=0 BUILD=0 SECRETS=0 CONFIGS=0 SWARM=0 SYSTEM=0 AUTH=0` and `ALLOW_START=0 ALLOW_STOP=0 ALLOW_RESTARTS=0`, though `POST=1 CONTAINERS=1 IMAGES=1` still permits `POST /containers/create` (a privileged container can be *created*, just not started through the proxy). `docker_marketplace` correctly targets `tcp://127.0.0.1:2375`, not the raw socket.
- **The HA integrations are bind-mounted, not copied**: the HA stack's `docker-compose.yml` mounts the integration source straight into `/config/custom_components/`. Those sources now live in `nivuus/marketplace` (and, archived, `nivuus/home-agent`) — update the compose mount paths to `packages/marketplace/...` when reworking the stack. A same-named directory under `/opt/nivuus/HomeAssistant/config/custom_components/` is a stale copy shadowed by the mount; editing it does nothing.

### HA service registration trap — moved with `nivuus/marketplace`

The `lambda`-swallows-the-coroutine trap in `hass.services.async_register()` was
found on `docker_marketplace` and is documented in **`nivuus/marketplace`**'s own
CLAUDE.md. It is a general Home Assistant lesson, worth reading before registering
a service from any integration: a plain lambda is typed `HassJobType.Executor`, so
HA discards the coroutine and the handler never runs. Use `functools.partial`.

### Cloud-gaming host = Apollo (Sunshine fork) + SudoVDA virtual display (2026-07-23)

- **The active streaming host is Apollo, NOT Sunshine.** Service `ApolloService` → `C:\Program Files\Apollo\tools\sunshinesvc.exe` (binaries keep the `sunshine.exe`/`sunshinesvc.exe` names — it's a fork). Active config: `C:\Program Files\Apollo\config\sunshine.conf`; active log: `C:\Program Files\Apollo\config\sunshine.log`. The old `C:\Program Files\Sunshine\` install is a **defunct leftover** (no running service; its `sunshine.conf` with `ResolutionAutomation`/`output_name`/`dd_configuration_option=verify_only` is NOT what runs — ignore it).
- **Apollo drives its own virtual display via bundled SudoVDA** (`C:\Program Files\Apollo\drivers\sudovda\`: `install.bat`, `uninstall.bat`, `nefconc.exe`, `sudovda.cer`, `SudoVDA.inf/.dll/.cat`). With `isolated_virtual_display_option = enabled` (+ `dd_configuration_option = ensure_primary`) Apollo creates a SudoVDA display at the **client's exact requested resolution/refresh on stream start** and isolates the physical outputs — so any client (5120×1440@120 32:9, 3840×2160@120, 2856×1280@90, 2410×1080@90…) gets the correct aspect natively, no fixed resolution list needed.
- **Ultrawide/wrong-aspect bug root cause (fixed 2026-07-23)**: SudoVDA was **disabled** (device `ROOT\DISPLAY\0003`, ProblemCode 22 = `CM_PROB_DISABLED` — merely disabled, not a broken driver), so Apollo could not build the per-client display and fell back to capturing the **physical HDMI dummy plug** (EDID `BBC0104`, capped at 3840×2160 16:9; widest wide-mode 2400×1080). A 5120×1440 request could not be set → desktop stayed at a 16:9/2400×1080 mode (log: `Desktop resolution [2400x1080]` + `Failed to change display modes … trying to set modes more strictly!`). **Fix: re-enable it** — `Enable-PnpDevice -InstanceId "ROOT\DISPLAY\0003" -Confirm:$false` (→ Status OK, Problem 0). If it ever regresses after a real reboot, reinstall cleanly via Apollo's `drivers\sudovda\install.bat`.
- **The HDMI dummy plug is HDMI 2.0 (max 4K)** → it physically cannot carry 5120×1440@120 (~26 Gbps) even with an NVIDIA custom-resolution/EDID override. A **virtual display (SudoVDA) is mandatory** for ultrawide-at-120; there is no dummy-plug workaround.
- **Do NOT install a second IDD virtual display driver** (e.g. MikeTheTech's MttVDD) — Apollo specifically controls SudoVDA, a competing IDD just confuses the `ensure_primary`/isolation logic. Keep exactly one virtual-display path (SudoVDA).
- **"Game runs but the stream shows only the desktop" = game launched on the physical dummy plug, not the streamed virtual display (fixed 2026-07-23).** With `dd_configuration_option = ensure_primary` + `isolated_virtual_display_option = enabled`, Apollo created SudoVDA (5120×1440) as a *secondary* pushed to the corner while the dummy plug (3840×2160) stayed **primary at (0,0)** — so fullscreen games opened on the dummy plug (offset 0,0) while Apollo captured the SudoVDA region (log: `Capture size 5120x1440 / Offset 3840x2160 / Virtual Desktop 8960x3600`). The virtual display is created by the app's `virtual-display = True` in `apps.json`, *not* by the isolated option. **Fix in `C:\Program Files\Apollo\config\sunshine.conf`: `dd_configuration_option = ensure_only_display`** (deactivate the dummy plug, leave SudoVDA as the sole display) **+ `isolated_virtual_display_option = disabled`** (no multi-monitor corner layout). Apollo semantics (from its locale strings): `ensure_only_display` = "Deactivate other displays and activate only the specified display"; `isolated_virtual_display_option` = "Move the Virtual Display to the bottom right-most corner … isolated from all other displays" (i.e. it *keeps* the physical display active — wrong for a headless box). `headless_mode` ("all apps start in virtual display") is an alternative lever, left off. Requires an `ApolloService` restart + a Moonlight reconnect to take effect. Config backup: `sunshine.conf.bak-20260723`.
- **"Desktop" app auto-maximizes the Steam window (2026-07-23).** The Apollo `Desktop` app (`apps.json`) launches plain `steam.exe` (NOT `-bigpicture` — user wants the normal client, just maximized) with a `prep-cmd` `do` that detaches `C:\Apollo-scripts\maximize-steam.ps1` (waits for the top-level window titled exactly `Steam` and repeatedly `ShowWindow(SW_MAXIMIZE)` for 30 s — Session-0/WinRM can't do this, it must run in the streaming session via the prep-cmd). `apps.json` hot-reloads on client connect (no restart needed). The separate `Steam Big Picture` app (`-bigpicture` + `nomousy`) is untouched. Backup: `apps.json.bak-20260723`. If Steam's main-window title ever changes, the matcher in the script must be updated.

- **Client/HDR ceilings measured 2026-08-22 — 4K120 is impossible, HDR is a no-op.** The TV client ("Télévision", Moonlight built into the TV, WiFi 5 GHz VHT80 @ -35 dBm / 866 Mbit/s PHY) fails 4K@120 with Moonlight **`error -5` at "video stream establishment"**: it aborts before emitting a single UDP packet, so Apollo logs `CLIENT CONNECTED` → `CLIENT DISCONNECTED` 300 ms later → `Initial Ping Timeout` and never builds a session encoder. **Read that signature as a client-side decoder failure, never as a network fault** — the host side succeeded completely (SudoVDA created at 3840x2160@120, NVENC H.264/HEVC/AV1 all probed OK, link 8x oversized for the 113 Mbps asked). The session history in `sunshine.log` is the factorial proof: 4K60 ✓, 1080p120 ✓, 4K120 ✗. A "4K 120 Hz" TV accepts 4K120 on its *HDMI input* while its **application decoder (MediaCodec) caps at 4K60** — 120 Hz is only reachable at reduced resolution. Use **4K60** or **1080p120**.
- **HDR cannot work on this VM — do not chase it in config.** *(Measured on Windows Server 2022; superseded for LTSC 26100 — see the next bullet.)* SudoVDA supports HDR only on **Windows 11 24H2** ("HDR is not supported on Windows 10"); the guest is **Windows Server 2022 build 20348 = Windows 10 21H2 base**. Every stream logs `HDR enable failed for display \\.\DISPLAY5` (plus `HDR revert failed` at teardown — the API fails in *both* directions), the display reports `"hdr_state": null`, and the captured desktop stays `Bits Per Color: 8` / `DXGI_COLOR_SPACE_RGB_FULL_G22_NONE_P709` (Rec.709) / `Max Luminance: 270 nits`. **The trap: requesting HDR still looks like it works.** Apollo sees `Client dynamicRange: 1` and dutifully encodes AV1/HEVC **10-bit** but with `Color coding: SDR (Rec. 709)` — the TV gets a 10-bit *SDR* stream it may render as washed-out fake HDR, costing bitrate and latency for zero gain. **Leave HDR OFF client-side.** `HKLM\SOFTWARE\SudoMaker\SudoVDA` (`hdrBits`) is absent = driver defaults, not a misconfiguration; setting it changes nothing on a Win10-based build. Real fix = move the guest to Windows 11 24H2 (Server 2025 shares the 26100/24H2 base but its Advanced Color support is **unverified**). **Alternatives investigated 2026-08-22, all closed**: swapping SudoVDA for `VirtualDrivers/Virtual-Display-Driver` (the only IDD advertising 8/10/12-bit HDR) hits the same wall — its maintainer states HDR exists "in the Windows 11 version, but not Windows 10"; a physical HDR dummy plug is dead too, since the installed **HDP-V104 EDID is a bare 128-byte block with zero CTA-861 extension** (no HDR static metadata block at all), and the OS-level API already fails regardless of display: probed from the **interactive** session, `DisplayConfigGetDeviceInfo(GET_ADVANCED_COLOR_INFO)` returns **rc=31 `ERROR_GEN_FAILURE`, supported=0, bpc=0**. To probe display APIs at all you must go through `schtasks /create /tn X /tr "cmd /c powershell -File ..." /ru Administrateur /it /f` + `schtasks /run` — over WinRM (session 0) `QueryDisplayConfig` reports `paths=0` even mid-stream, while the same call in session 1 reports `paths=1`. Only **one** IDD is live (`ROOT\DISPLAY\0003` = SudoVDA, status OK); the `MTT1337` ("VDD by MTT") EDID under `HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY` is a **stale registry leftover**, not a second active driver — do not "fix" it.

- **HDR VERDICT — Windows 11 IoT Enterprise LTSC 2024 (build 26100) REMOVES the blocker. Measured 2026-08-22 on the real passthrough GPU.** A throwaway domain `Windows-LTSC-test` was installed unattended from the LTSC medium (Secure Boot + vTPM 2.0 + RTX 4070 passthrough, all three verified together for the first time), the NVIDIA 610.88 driver and SudoVDA were provisioned, and the Advanced Color probe ran in **session 1**:

  ```
  sizes rc=0 paths=2 modes=4
  target=0     rc=0 supported=0 enabled=0 bpc=8 outputTechnology=4294967295 name=
  target=12544 rc=0 supported=1 enabled=0 bpc=8 outputTechnology=5          name=HDP-V104
  ```

  **`rc=0`, against the `rc=31 ERROR_GEN_FAILURE` this host has always returned.** That single field is the verdict: `DisplayConfigGetDeviceInfo(GET_ADVANCED_COLOR_INFO)` now *works*. Reading only `supported=` conflates a healthy "this display is not HDR" with a broken "the API failed" — the two differ in `rc` alone. `target=12544` is the HDMI dummy plug on the passed-through card (`outputTechnology=5`, EDID name `HDP-V104`); `target=0` with no name is the emulated QEMU VGA the domain carries so the unattended install is not blind.

  `supported=1` on the dummy plug is better than predicted. **`enabled` stays 0 and `bpc` stays 8** even after a `Win+Alt+B` toggle: the HDP-V104 EDID is a bare 128-byte block with no CTA-861 extension, so Windows reports the stack as capable but cannot light HDR on a sink that never advertises receiving it. Getting `enabled=1 bpc=10` needs a real HDR sink (a cable to the TV) or a SudoVDA virtual display — the latter only exists once Apollo starts a stream, which is sub-project B.

  **Activation works**: `slmgr /dli` → `IoTEnterpriseS edition, VOLUME_MAK channel, License Status: Licensed`. That closes the spec's "a retail-resold IoT LTSC key may not activate" risk.

  Cosmetic trap: the registry `ProductName` reads `Windows 10 IoT Enterprise LTSC 2024` on this build. The HAL and `ver` both say `10.0.26100`, i.e. Windows 11 24H2 — do not read the product string as evidence of the wrong OS.

- **HDR CONFIRMED END-TO-END ON THE SudoVDA VIRTUAL DISPLAY (2026-08-22, second throwaway guest).** The A verdict left one link unmeasured — the virtual display only exists while Apollo streams, so it could not be probed then. It has now been measured, on the real passthrough GPU, with Apollo 0.4.6 installed and a Moonlight client driving a live stream:

  ```
  [avant] target=256 rc=0 supported=1 enabled=0 bpc=8  name=nivuus-probe
  set     target=256 rc=0
  [apres] target=256 rc=0 supported=1 enabled=1 bpc=10 name=nivuus-probe
  ```

  **`enabled=1` and `bpc=10`** — real 10-bit HDR, against the `enabled=0 bpc=8` ceiling the HDMI dummy plug can never pass. `paths=1`: with `dd_configuration_option = ensure_only_display` the virtual display was the *only* active one, both the dummy plug and the emulated VGA deactivated. Apollo's own log agrees (`Display is HDR: true`). Identity is nailed down, not deduced: `DISPLAY\SMKD1CE\…UID256_0`, manufacturer `SMK` = SudoMaker, `UID256` = the probed `target=256`.

  **The dummy plug is therefore obsolete for HDR and can be unplugged.** Its EDID is a bare 128-byte block; SudoVDA's, extracted straight from `SudoVDA.dll` at offset `0xd090`, carries a full CTA-861 extension: HDR Static Metadata block with `ET=0x0f` (gamma SDR + gamma HDR + **PQ/ST 2084** + **HLG**), SMD type 1, max luminance ~3800 cd/m², plus a Colorimetry block advertising **BT.2020** RGB/YCC/cYCC. That EDID can be read offline from the driver file — no VM needed to re-check it.

  ⚠️ **Two things the measurement does NOT settle.** (1) The probe *forced* the state with `DISPLAYCONFIG_SET_ADVANCED_COLOR_STATE` (type 10) rather than waiting on the client, deliberately: the host-side Moonlight decodes in software and its `dynamicRange` flickered 0/1, which would have produced a false negative indistinguishable from a real one. What is proven is that **Windows 26100 can light HDR on this display**; that the TV's request drives it end-to-end is a separate, smaller question. (2) **Cold boot with zero displays is still untested** — the throwaway domain carries an emulated QEMU VGA that the production domain lacks, so "no dummy plug, no stream running" never occurred. Cheap mitigation for the new domain: **keep an emulated video device**. The two coexist fine and Apollo deactivates the VGA during streams, as `paths=1` shows.

  Two side findings from the same run. **Apollo 0.4.6 dropped HTTP Basic on `/api/*`**: `GET /api/config` and `POST /api/pin` both return 401 with Basic, and authentication now goes through `POST /api/login` → `Set-Cookie: auth=…`. The Pomerium route for `game.allanic.me` injects `Authorization: Basic …` (`/opt/nivuus/Pomerium/config.yaml`), so **that route breaks the day Apollo is upgraded** — plan a cookie/session path or keep the pinned version. And the SudoVDA driver is the **`Virtual Display Driver (HDR)`** build (its PDB path says so), declares `UmdfExtensions = IddCx0102`, and reads a `hdrBits` registry knob — the low IddCx revision did *not* prevent HDR, so do not treat it as a blocker.

  ✅ **The 24H2 OOBE fix is VERIFIED (2026-08-25).** With `Microsoft-Windows-International-Core` in the `oobeSystem` pass, an ISO rebuilt by `build.py` walks from boot to the logged-on desktop with **zero keypresses** and the French locale applied. The measurement is a controlled pair the same afternoon: the 22/08 ISO (without the component) stopped on "Is this the right country or region?" then on the keyboard page and needed three `virsh send-key KEY_ENTER`; the rebuilt one stopped on neither.

- **`winvm` quoting is solved: use `powershell -EncodedCommand <base64 UTF-16LE>`.** `winvm` joins `$*` into a single cmd string, so nested quotes/`$` in PowerShell one-liners get mangled (`$l=Get-Content` arrives as `=Get-Content`; a `|`-separated `-Pattern` splits into separate commands). Encode instead: `B=$(iconv -f UTF-8 -t UTF-16LE s.ps1 | base64 -w0); winvm "powershell -NoProfile -EncodedCommand $B"`. Budget **~3 min** for anything using `Add-Type` (C# compile over WinRM) — a 2 min timeout kills it mid-compile. Caveat: WinRM runs in **session 0**, where `QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS)` returns **0 paths even mid-stream**, so display-topology probing must run in the interactive session (scheduled task with `/IT`), never over WinRM.

### Ollama dockerisé + API universelle sécurisée (2026-07-17)

- **ollama tourne en Docker** (`/opt/nivuus/ollama/docker-compose.yml`): conteneur `nivuus-ollama` (host network, bind **127.0.0.1:11434**, GPU via `deploy.resources`, keep_alive=-1, **OLLAMA_CONTEXT_LENGTH=16384 + KV cache q8_0** (les clients /v1 type Qwen Code ne demandent pas de num_ctx → sans ça, défaut 4096 et troncature silencieuse), modèles montés depuis `/usr/share/ollama/.ollama`) + `nivuus-ollama-proxy` (nginx, **:11435 = API universelle compatible OpenAI `/v1`**, auth Bearer; clé dans `/root/.config/nivuus/ollama-api.key` et `.env`, mode 600). Le service systemd `ollama` hôte est **disabled** (binaire conservé). WAN bloqué par les zones firewalld (external/public REJECT), LAN home autorisé.
- **Cycle GPU**: hook `bind-vfio-gpu.sh` fait `docker compose stop ollama` (plus de relance CPU pendant le gaming — l'ancien hook relançait ollama en CPU, gâchant le budget RAPL 50 W); hook `rebind-host-gpu.sh` fait `docker compose up -d` (recrée avec GPU). Self-heal dans `vm-idle-shutdown.sh`: VM off + conteneur down → `up -d` (couvre le boot). `gpu-off.override.yml` permet un lancement CPU manuel (`-f docker-compose.yml -f gpu-off.override.yml`).
- **Modèle**: `qwen3:14b` (9,3 GB) pour contrôle domotique/tool-calling. **Intégré dans HA** (intégration ollama native, entry `01KXQZ94NA6J956HDKSW72KZG5` → `conversation.qwen3_local` avec **llm_hass_api=assist** (contrôle domotique actif), think=false, num_ctx=8192 + `ai_task.qwen3_ai_task`). Config par API: flows REST `/api/config/config_entries/flow` + subentries `/api/config/config_entries/subentries/flow` (reconfigure = même POST + `subentry_id`); liste subentries via WS `config_entries/subentries/list`. Testé E2E: 9,7 s sur CPU; attendu 1-2 s sur GPU. NB: pendant que la VM a le GPU, l'agent HA local est down/lent → prévu de basculer sur Gemini (automatisation à faire).

See `docs/system-audit.md` for complete infrastructure documentation.

### Hard freezes: platform halts with power intact, RAM prime suspect (updated 2026-08-16 — the WiFi/ASPM theory is DEAD)

Whole-machine freezes — Jul 23 ~12:50, **Aug 4 19:30**, **Aug 5 03:50**, **Aug 7 18:23** — the first three each leaving a **FATAL firmware error record in ACPI BERT** (3 CPER sections, all `Firmware Error Record Reference`, severity FATAL; the kernel prints `BERT: [Hardware Error]: Skipped 1 error records` because the region is 3444 B > the 1 KB print cap — dump it with `dd if=/dev/mem skip=$((0x<addr>))`, address+length are at offsets 0x28/0x24 of `/sys/firmware/acpi/tables/BERT`). **BERT presence is a reliable crash oracle here**: present after both freezes, absent after the clean poweroff in between.

**Only the Aug 4 freeze left a kernel trace, and it is unambiguous**: `AER: Multiple Correctable error … from 0000:0a:00.0` → `ath10k_pci 0000:0a:00.0: firmware crashed!` → ~30 × `AER: Multiple Uncorrectable (Fatal) … from 0000:09:03.0` over 10 s → register reads return `4294967295` (link dead) → journal stops, whole box gone (**WAN included** — user confirmed nothing answered, not even ppp0). Topology: root port `00:1d.0` → **ASMedia ASM1182e x1 Gen2 packet switch** (`08:00.0` upstream, `09:03.0`/`09:07.0` downstream) → the two QCA9984. Both WiFi cards share that single x1 link.

**The ASPM hypothesis was REFUTED on 2026-08-16 — do not resurrect it.** The theory was ASPM L1 on the `00:1d.0 ↔ 08:00.0` link (the only ASPM-active link in the chain; ASPM L1 on ASMedia switches is a known source of link-recovery failures). The pre-registered falsification criterion was met exactly: ASPM L1 was disabled on 2026-08-05, the guard then ran for the whole Aug 5→7 uptime logging **zero AER errors** (9959 guard lines in syslog, not one `PCIe AER on`), **and the box froze anyway on Aug 7 18:23**. The AER storm of Aug 4 was therefore a *symptom*, not the trigger — the Aug 5 and Aug 7 freezes have no PCIe trace whatsoever. **Two reasoning errors are worth remembering**: (1) "the freezes happen at idle" was a misreading — 81 % idle across 24 CPUs is 3.4 cores busy, and the four freezes had load averages of 4.2 / 9.0 / 21.9, i.e. no idle correlation at all; (2) the guard, its units and its 16 tests are all still deployed and harmless, but they answer a question that turned out not to be the question.

**The hardware watchdog does NOT save this box from it**: iTCO 30 s is correctly armed and petted by PID 1, yet the machine stayed dead 4 h (03:50→07:45) with no reset attempt in wtmp. So the freeze halts the platform itself, not just the kernel — **prevention is the only lever, there is no automatic recovery to fall back on.**

**Deployed**: `scripts/pcie-wifi-link-guard.sh` → `/usr/local/sbin/nivuus-pcie-wifi-link-guard.sh`. `apply` disables ASPM L1 across the chain via the kernel's own `link/l1_aspm` sysfs (not `setpci` — the kernel then keeps both link ends consistent and survives its own ASPM re-evaluation); `check` samples AER counters and logs any change to **stderr** so journald syncs it to disk immediately (a buffered message would be lost in the freeze it exists to explain); `status` dumps the chain. The chain is **derived from sysfs** (`ath10k_pci` driver links walked upward), never hardcoded, and every path is fail-open. Units: `nivuus-pcie-link-guard.service` (boot, oneshot) + `nivuus-pcie-link-check.timer` (1 min). The boot unit deliberately carries **no `After=multi-user.target`** (the 2026-07-16 ordering-cycle trap) — the script waits for the ath10k devices instead. Tests: `scripts/tests/test_pcie_wifi_link_guard.sh` (16 assertions against a fake sysfs tree, since AER errors can't be injected on demand).

**Current standing (2026-08-16): the platform halts while still energised — POST/DRAM-training failure, RAM is the prime suspect.** Full boot history from **`wtmpdb`** (Debian 13's second boot database — `last`/`wtmp` alone is NOT enough, `wtmpdb last` showed records `last` did not): `23/07 12:56 → crash 04/08 19:30` (12 d), `04/08 19:56 → crash 05/08 03:50` (8 h), `05/08 07:45 → crash 07/08 18:23` (2 d 10 h), then **07/08 ~18:30 the machine did not boot at all** (zero trace in `wtmp`, `wtmpdb`, `kern.log`, or `boot.log` rotation — it never reached Linux) for nine days, and booted normally on 16/08.

**Do NOT conclude "PSU died" from that gap — measure it.** `smartd` writes timestamped raw SMART values to `/var/lib/smartmontools/attrlog.*.csv` (the `.state` files only hold the latest, but the CSV is a full history and survives reboots — it is the only retrospective hardware timeline on this box). On sdb, attribute 9 Power_On_Hours went **20040 (07/08 18:15) → 20251 (16/08 14:35): +211 h against 212 h of wall clock**, with attribute 12 Power_Cycle_Count only +2. **The disks spun through virtually the whole outage**: the PSU never stopped delivering, so a protection trip / power loss is ruled out, and the machine sat *powered but unable to POST* for nine days. (Use sdb: sda's attribute 9 raw is a packed Seagate field and decodes to nonsense.)

Everything above the hardware is eliminated for the 07/08 freeze: no AER, ASPM already off, CPU peak 70-77 °C against a 95 °C ceiling, RAPL capped 60/68 W, SMART clean on all four disks, 33 G RAM available, 0 oomkill. **The decisive artefact is the last HA state 7 s before the cut**: CPU 59 °C, PCH 72 °C, fans steady 1102/1322 RPM, NVMe 52.9 °C — flat for 15 minutes. Nothing gradual can produce that. The iTCO watchdog (30 s, armed, petted by PID 1, `bootstatus=0`) never fired on **any** freeze, which proves the PCH itself stops executing.

**Remaining suspects, in order**: **RAM / DRAM training — now first, and the one thing no instrumentation here can see.** It is the only candidate that explains *both* symptoms at once: an instant halt with power intact, and then nine days of powered-but-no-POST (Alder Lake retries memory training indefinitely on a marginal DIMM or socket contact). The board is non-ECC and `igen6_edac` loads but registers no controller, so `/sys/devices/system/edac/mc` stays empty and a memory fault is **physically invisible in software** — only an offline memtest86+ run can rule it in or out. Then: motherboard VRM / CPU socket / CMOS state; then a PSU delivering *out-of-spec but non-zero* voltage (demoted, since it demonstrably never cut out — and no UPS is connected, `/etc/nut` names an APC that does not exist with `MODE=none`, so mains quality stays unmeasured).

**Instrumentation deployed 2026-08-16** so the next freeze is not blind:
- `scripts/hw-blackbox.py` → `/usr/local/sbin/nivuus-hw-blackbox.py`, unit `nivuus-hw-blackbox.service`. Samples every rail, PECI/PCH/NVMe temps, fans, RAPL watts, load and the MCE/THR counters **once per second and `fsync`s each line**, so the last line on disk is the last moment the machine was alive. `/var/log/nivuus-blackbox.csv` (+ 2 rotations, ~20 h each). 26 tests in `scripts/tests/test_hw_blackbox.sh`.
- **nct6798 rails have no labels**; the canonical nct679x mapping (+12V = `in1`×12, +5V = `in4`×5) yields 11.90 V / 5.00 V here, which corroborates it but does not prove it. So **alerting is mapping-agnostic**: each rail learns its own median + p1/p99 range over an hour (`/var/lib/nivuus/blackbox-baseline.json`) and only excursions *outside that range* count. **The range is not optional** — Vcore legitimately swings 0.62-0.87 V with load, so a symmetric band or raw deviation pegs the metric at ~35 % and buries a real 2 % sag on a fixed rail.
- MQTT agent feature `hardware_health` (`nivuus/mqtt:src/features/health/`) surfaces it in HA: `sensor.nivuus_power_psu_12v_rail_sensor`, `..._psu_5v_...`, `..._cpu_vcore_...`, `..._max_rail_drift_...`, `..._cpu_machine_check_errors_...`, `..._cpu_thermal_throttle_events_...`, `..._memory_errors_...` (deliberately `unavailable`, with an attribute saying why — a silent 0 would read as "no memory errors" when the truth is "memory errors cannot be seen"), `..._disk_hardware_errors_...` (11 = 4 NVMe media errors + 4 log entries + 3 SATA CRC).
- Tdarr throttled 4→2 CPUs per node in `/opt/nivuus/MediaManager/docker-compose.yml` (backup in `/media/backup/`). Load reduction during observation, **not** a fix — Tdarr was transcoding at all four freezes, but it transcodes nearly always, so the correlation is weak.
- **Crash forensics are perishable**: `/var/log/journal` lost every archived boot (only rsyslog's `kern.log`/`syslog` survived, so use those), and the recorder DB (`purge_keep_days: 5`) only still held the crash windows because the box was down. The 2 h before each freeze are preserved in `/media/backup/crash-forensics-20260816/`.

**Also flagged, not acted on**: `/media/data` and `/media/backup` are mounted `data=writeback,barrier=0` (see `/etc/fstab`) — with repeated instant power cuts that is a real data-loss exposure. (The swapfile *used* to live on `/media/backup`; it was moved to the NVMe on 2026-08-24, see below.) And the 9 `winvm-proxy-*.socket` entries in `/etc/systemd/system/sockets.target.wants/` are regular files, not symlinks, so systemd ignores them (`is not a symlink, ignoring`).

### Swap thrashing outage 2026-08-22→24: one capped container froze the whole host

Symptoms over ~36 h: WiFi clients dropping, HTTP/SSH/Internet intermittently dead, load 101. **Not a hardware freeze** — a swap-thrashing cascade. Causal chain, each link measured:

1. `mediamanager-tdarr-node-nvenc-1` is configured (server-side, Tdarr UI) with **`transcodegpu: 5`** workers inside `mem_limit: 4g`. Five concurrent ffmpeg at 1-2.5 GB each cannot fit.
2. The cgroup pegged at its limit (`memory.events max = 251075`) but **never OOM-killed** (`oom_kill 0`) because `memory.swap.max` allowed 4 GB of swap. It thrashed instead: **22 M major faults, 28.7 M anon refaults**.
3. The swapfile was on `/media/backup` = **sda = ST5000LM000, a 2.5" SMR drive**. Measured swap throughput there: **~0.7-1 MB/s**, `r_await` 190 ms, `w_await` 289 ms, queue 24, **%util 100 at ~96 IOPS of 4 kB**. One container consumed the machine's entire swap device.
4. Everything else stalled behind it: `io pressure full 53 %`, `memory pressure full 53 %`, 91 threads in D on `rq_qos_wait`.

**The definitive evidence is in the journal, and it is the diagnostic reflex to remember**: `systemd-journald.service: Watchdog timeout (limit 3min)!` ×12, plus **53 × `Processes still around after SIGKILL. Ignoring.`** — daemons frozen in D state that *even SIGKILL cannot reap*. `systemd-resolved` frozen ⇒ DNS dead; `systemd-logind` frozen ⇒ no new SSH session; `NetworkManager-dispatcher` frozen ⇒ WiFi/PPPoE. That maps 1:1 onto "everything is intermittently unreachable". **When the whole box is slow, read `/proc/pressure/*` first** (`full` = fraction of time *all* tasks are stalled), then `for f in $(find /sys/fs/cgroup -name memory.pressure)` to localise the offending cgroup in one pass — it found the container immediately.

Also stuck: 5 orphaned `tdarr-ffmpeg` running **40 h** with their output files already `(deleted)` (Tdarr reported `actifs 0` on that node), and two *duplicate* jobs on the same source file. `docker stop` on the node turned them into zombies (RSS 0) and freed the memory instantly.

**Swapfile moved to the NVMe, 64 GB → 8 GB (2026-08-24).** Now `/swapfile` on `/` (ext4/LVM/NVMe), `pri=10`, declared in `/etc/fstab`. **Size matters as much as location**: 64 GB of slow swap is exactly what let a runaway container thrash for 40 h instead of being OOM-killed in seconds. Create it with `dd`, **not `fallocate`** — on ext4 fallocate leaves unwritten extents and `swapon` refuses with `swapfile has holes`. Migration order: `swapon` the new one at higher priority *first*, then `swapoff` the old, so pages move to RAM/NVMe rather than nowhere. Budget the `swapoff`: reading 12 GB back off the SMR took **~3 h at 0.9 MB/s**, and it must be run detached (`setsid nohup …`) or a session teardown SIGKILLs it mid-way (harmless — the device just stays partly populated, relaunch it). NVMe wear is a non-issue: Samsung 980 at 21 % used, 100 % spare. `vm.swappiness` was already 10.

**The chronic swap filler was `/tmp`, not memory pressure.** `/tmp` is a **10 GB tmpfs** (fstab) and was **91 % full (9.1 GB)** while `Shmem` in RAM was only 467 MB — i.e. nearly all of it had been pushed to swap. That is why atop shows a flat 10-12 GB of swap for weeks *before* the incident. 5.6 GB of it was `/tmp/user/0/claude-0/` — **74 stale Claude Code session scratchpads**; deleting 72 of them (keeping any session with a live fd) freed **2.7 GB of swap slots instantly**. Deleting a tmpfs file releases its swap slots, so this is the fast way to shrink a `swapoff`. Worth a periodic bound in `disk-maintenance.sh`.

**Aggravators found in the same window (Sunday 23/08):** `clamav_targeted_scan.sh` (cron `30 4 1 * 0` = every **Sunday 04:30** *and* the 1st of the month; it has **no flock guard**) was caught in the saturation and ran 29 h in D state; and root's crontab runs **`e4defrag /dev/sda1 /dev/sdb1` every Sunday 06:00** — a defrag on the SMR drive, which is actively counterproductive there (band rewrite amplification for no gain). Both were victims/amplifiers, not causes.

**`handle-vm-start.sh`: an unreachable hypervisor was read as a transitional state (fixed 2026-08-24).** `VM_STATE=$(LANG=C virsh domstate "$VM_NAME" 2>/dev/null)` discards stderr, so a dead libvirtd yields `""`, which fell through to the "transitional state" branch → **90 s wait → exit 1 → re-triggered by the next Moonlight probe, forever** (observed: a wake every 91 s from 00:33 to 09:44). The script now has `query_vm_state()` which propagates virsh's exit code and aborts immediately with the real cause. Source is now **versioned in `scripts/handle-vm-start.sh`** (it previously existed only as a deployed file) with `scripts/tests/test_handle_vm_start.sh` (10 assertions against a fake `virsh`; the script honours `VM_TRIGGER_LOCK` so tests don't touch the production lock). Note `VM_PORT` is still hardcoded to 47984 even when invoked for a 47989 wake — harmless today because the libvirt `started/begin/rules.sh` hook installs all forward-ports anyway.

**libvirtd died in the same cascade** and could not restart: `Impossible d'acquérir le fichier pid '/run/libvirtd.pid'` + `Found left-over process … (virtiofsd) in control group`. Four **orphaned `virtiofsd`** from VMs dead 3.3 days were still in libvirtd's cgroup. Kill them (no qemu alive ⇒ they are orphans), then `ResetFailedUnit` + `StartUnit` over D-Bus. Recovery took seconds — do not reach for the flock/`mv the pid files` procedure until you have confirmed a *live* zombie still holds them.

**fail2ban: one invalid jail kills the entire daemon (found 2026-08-24).** It had been **failed since at least 22/08 10:55** — i.e. SMB 445 and SSH exposed on the WAN with no protection — because Vaultwarden had been removed (`/opt/nivuus/Password` no longer exists) while `[vaultwarden] enabled = true` remained in `jail.local`: `ERROR Failed during configuration: Have not found any log file for 'vaultwarden' jail` → `status=255`, **no jail loads at all**. Set `enabled = false`; `fail2ban-client -t` validates before restarting. **Rule: after removing any service, grep `/etc/fail2ban/jail.local` for a jail pointing at its log.** Intrusion review over the whole window came back clean: every successful SSH is `publickey` for `mallanic` (password auth and root login are off, `AllowUsers mallanic`), ~1000 brute-force attempts all failed, no new accounts, single UID 0, `authorized_keys` untouched since 2025-09-04, no SUID modified in 30 days, CrowdSec with zero decisions.

### RF / Radio Frequency Map (2.4 GHz coexistence plan)

Audited 2026-07-16 — current layout has **zero overlap**, preserve it:
- **WiFi 2.4 GHz**: QCA9984 (`phy2`, `wlp11s0`+`wlp11s1`) — **ch 6 pinned** (2427–2447 MHz), 20 MHz, 20 dBm (also forced by an `ExecStartPost` in hostapd.service). 2026-07-16: `channel=6` pinned (was `0` = ACS, which could land on ch 9–11 = on Thread) and `[HT40+]` removed (its secondary channel ch 10 @ 2457 MHz would cover Thread) in `/etc/hostapd/2.4Ghz.conf`. Card EEPROM regdom is US → ch 12/13 unavailable. NB: `systemctl reload hostapd` (HostapdManager's apply path) cannot apply channel/BSS changes — those need `restart` (~10 s outage, clients re-attach automatically).
- **WiFi 5 GHz**: QCA9984 (`phy1`, `wlp10s0`+`wlp10s1`) — ch 36 fixed, VHT80 centered ch 42 (5170–5250 MHz, UNII-1 non-DFS), 23 dBm. Cards support VHT160 but that requires DFS channels (radar interruptions) — 80 MHz is the deliberate stability choice.
- **Zigbee**: zigbee2mqtt, **ch 25 (2475 MHz)**, 20 dBm, `adapter: zstack` on SMLIGHT **SLZB-MR2U** USB stick interface `if02` (`ttyACM2`).
- **Thread**: OTBR container (host network, `wpan0`), **ch 21 (2455 MHz)**, txpower **+20 dBm** (stick max, ETSI-legal; was 0 until 2026-07-16 — raised because child 0xe807 at RSSI −77 dropped downlink frames). otbr-agent does NOT persist txpower — it is applied by the `command` override on the `otbr` service in `/opt/nivuus/HomeAssistant/docker-compose.yml` (s6-overlay runs it after services; loop re-applies every 5 min). Thread dataset persists in `/opt/nivuus/HomeAssistant/otbr` → recreating the container is safe. RCP on the **same SLZB-MR2U stick** `if00` (`ttyACM0`) — it is a dual-radio stick.
- **Bluetooth**: Intel AX201 (`hci0`, name `nivuus`), BLE central+peripheral, AFH across 2402–2480. The AX201 WiFi half (`wlo1`) is unused (down, NM-unmanaged).
- No RF hardware passed to the Windows VM (GPU + audio + one Samsung NVMe only). No DVB/Z-Wave/LoRa hardware.

### VM Wake-on-Demand (cloud gaming)

`vm-trigger-47984.socket` **and `vm-trigger-47989.socket`** (added 2026-07-17 so that merely *opening* Moonlight — which polls serverinfo on 47989 HTTP and/or 47984 HTTPS — wakes the VM; clients must target the HOST LAN IP `192.168.0.1`, not the VM IP, and re-pair once against that entry) (systemd socket-activation, host `0.0.0.0`, `Accept=false`) trigger the oneshot `vm-trigger-{port}.service` → `/usr/local/sbin/vm-wake-gate.py` (**wake gate**, 2026-07-17, source of truth now `scripts/vm-wake-gate.py` + `scripts/tests/test_vm_wake_gate.py`: accepts the pending connection, reads the first bytes, only chains to the wake script if the client speaks Moonlight; port scans/mute connections are logged `wake REJECTED from <ip>` and ignored; syslog tags `vm-wake-gate-{port}`) → `/usr/local/sbin/handle-vm-start.sh`: starts the Windows VM if shut off, waits for its IP (agent then DHCP lease, 180s max), then adds a **runtime** forward-port in the **default** firewalld zone.

**The wake sockets ARE internet-exposed while the VM is off (CRITICAL, found 2026-07-24 — the opposite of what this file claimed).** The belief that "the permanent `external` DNAT `47984/47989→192.168.3.2` means WAN traffic never reaches the host socket" is **wrong**: the libvirt hook `qemu.d/Windows/stopped/end/rules.sh` deletes those forward-ports **from every active zone** when the VM stops (they are re-added by `started/begin/rules.sh`). So exactly when the wake path is armed, the DNAT is gone and the whole internet can reach `0.0.0.0:47984/47989` on `ppp0`. Consequence: **the 47984 test (`data[0] == 0x16`, i.e. "client speaks TLS") matched every mass scanner on the internet.** Over the 30 days to 2026-07-24, *all* wakes it produced were false positives (Driftnet, Linode, Akamai, Datacamp, …) and *zero* came from a real client; the only genuine wakes came from 47989 with `GET /serverinfo`. **Fix: 47984 is out of the wake path** (probes still logged); 47989's HTTP signature is the only wake trigger — proven discriminator, 42 scanner probes rejected, 0 false positive. Remote (off-LAN) wake still works because Moonlight also probes 47989. Residual risk: a scanner that deliberately speaks Sunshine/Moonlight HTTP would still wake the VM — for strict control add a firewalld source-IP allowlist (breaks roaming clients). Diagnostic reflex: `journalctl -t vm-wake-gate-47984 -t vm-wake-gate-47989 | grep accepted` shows the source IP of every wake; `grep dport=4798 /proc/net/nf_conntrack` shows the live scan traffic. `Type=oneshot` + repeated triggering (even *successful* runs — systemd counts starts, not failures: ≥5 Moonlight polls in 10 s during a VM boot window trip it) killed the socket via `service-start-limit-hit` (2026-07-13, again 2026-07-17). **Fixed permanently 2026-07-17**: drop-ins `no-start-limit.conf` (`StartLimitIntervalSec=0`) on both trigger services, `flock` single-instance guard in `handle-vm-start.sh`, and transitional-state handling (waits up to 90 s for `in shutdown`/hibernation to settle instead of exit 1). The idle-check timer also self-heals both sockets every 10 min whenever the VM is off. Manual recovery if ever needed: `systemctl reset-failed vm-trigger-{47984,47989}.{service,socket} && systemctl start vm-trigger-{47984,47989}.socket`.

**`headless_mode` est INTERDIT dans la config Apollo (tranche le 2026-08-27).** Il fait taire un « Fatal: Unable to find display or encoder during startup » qui est une FAUSSE alarme — au repos aucun ecran ne vit sur la 4070 que `adapter_name` epingle, la sonde d'encodeurs echoue donc sur les quatre encodeurs *y compris le logiciel*, ce qui prouve que le GPU n'est pas en cause ; les encodeurs sont re-enumeres des qu'un ecran parait. Mais **il casse le streaming vers le telephone** : avec un ecran deja present, Apollo reconfigure le mode d'un ecran existant au lieu d'en creer un a la resolution demandee, et le client Android sort 0,5 s apres `CLIENT CONNECTED`. Cote telephone le logcat dit `IllegalArgumentException: The surface has been released` dans `MediaCodec.configure()`, quatre tentatives, puis `Video stream start failed: -5` — et Moonlight ferme *proprement*, donc **sans aucun message d'erreur a l'ecran**, ce qui rend le symptome muet. Chronologie : activé 22:20:16 le 26/08, premier echec 22:25:02, le 2410x1080 refonctionne des le retrait. **Le Chromebook et la TV (3840x2160x60) n'ont jamais bronche** — c'est ce qui a fait chercher la cause cote client pendant des heures. Bilan retenu : on garde la banniere cosmetique plutot que le bug. Diagnostic si elle revient : si l'encodeur LOGICIEL echoue lui aussi dans `sunshine.log`, il manque un ecran, pas un GPU. Le test `scripts/tests/test_windows_guest_apollo.py` verrouille l'absence de la cle.

**Piste voisine, non elucidee** : bug amont ouvert [moonlight-android#1589](https://github.com/moonlight-stream/moonlight-android/issues/1589), meme exception pour toutes les resolutions non-16:9 sur Pixel. Il decrit le meme symptome mais n'etait pas la cause ici.

**La surveillance de Steam ne doit jamais attendre derriere autre chose (2026-08-27).** `steam-session.ps1` est la commande SUIVIE par Apollo : sa sortie ferme la session Moonlight. La maximisation de la fenetre et la surveillance de Steam tenaient dans deux boucles successives — maximiser d'abord, surveiller ensuite. Un Steam **deja lance et replie dans la zone de notification n'a pas de fenetre principale**, donc pas de titre `Steam` a reconnaitre : la premiere boucle allait au bout de ses 180 s, et quitter Steam ne fermait rien pendant tout ce temps. Mesure : session a 11:16:05, `Process terminated` a 11:19:08 — 180 s de boucle plus les 3 s de grace, au dixieme pres. Fusionnees en **une seule boucle** qui surveille toutes les 500 ms et greffe la maximisation dessus (`$maximized`, `Set-SteamMaximized`). En Big Picture la fenetre est deja plein ecran, on part donc avec le travail fait. Regle generale : dans ce script, toute tache ajoutee se greffe sur la boucle de surveillance, jamais devant elle.

### WAN / PPPoE (Orange/Sosh fibre) — CRITICAL

The WAN is a PPPoE session over VLAN 835. **The physical WAN port is now `enp5s0` → VLAN `enp5s0.835`** (was historically `enp6s0.835`; the cable/port moved). NM still manages the VLAN `enp5s0.835` and brings it up at boot.

**Boot persistence is handled by NetworkManager** (profile `pppoe-enp6s0.835`, autoconnect=yes, zone=external):
- The **critical config** for PPPoE-over-VLAN with NM is to set **BOTH** `connection.interface-name=ppp0` (the ppp device NM creates) **AND** `pppoe.parent=enp5s0.835` (the VLAN). NM does support a VLAN parent (the earlier "RH bug 1663719 / unsupported" belief was wrong — the real cause was an *empty* `connection.interface-name`, which yields `error determine name for pppoe`; setting `interface-name` to the VLAN device instead yields `No suitable device found ... mismatching interface name`). Validated 2026-05-25.
- The HA `PppoeCredentials.ts` integration edits this nmconnection file for credentials; NM now actually brings up the link.
- **Fallback (kept, disabled):** systemd unit `/etc/systemd/system/pppoe-dsl.service` runs `pppd call dsl-provider persist holdoff 60 maxfail 0 nodetach` over peer file `/etc/ppp/peers/dsl-provider` (`pty "pppoe -I enp5s0.835 ..."`, `user "fti/CHANGE_ME"`, CHAP via `/etc/ppp/chap-secrets` + `/etc/ppp/pap-secrets`). If NM ever fails: `nmcli con down pppoe-enp6s0.835 && systemctl start pppoe-dsl.service`. Do NOT run both at once (they fight for the enp5s0.835 PPPoE session).

**Orange BNG reconnect cooldown (IMPORTANT):** after a clean pppd teardown (SIGTERM → PADT), Orange holds the session ~3-5 min and rejects fast reconnects (CHAP fails even with the right password). **Never hammer reconnects** — that is why the unit uses `RestartSec=120` + pppd `holdoff 60` + `persist` (persist redials without a clean PADT). When recovering manually after a kill, wait ~5 min before relaunching.

**Firewalld zone for ppp0:** `ppp0` is dynamic and must be placed in the `external` zone, done by `/etc/ppp/ip-up.d/firewalld-external` on every link-up. Without it, ppp0 falls into the default `internal` zone (too permissive for WAN) AND the external-zone forward-ports (cloud-gaming → 192.168.3.2) don't apply. firewalld uses the **nftables** backend (`table inet firewalld`) — `iptables -t nat -S` looks empty but isn't; check `nft list table inet firewalld`. `net.ipv4.ip_forward` is already persistent in `/etc/sysctl.conf`.

**WAN receive processing is pinned to the E-cores (2026-08-26).** As far as the NIC is concerned a PPPoE session is one flow, so every inbound packet landed on one hardware queue and one CPU — and that CPU was the ceiling, not the line. Measured against `ash-speed.hetzner.com`, four alternating passes: **147/121/137/122 Mbit/s with RPS off** (cpu17 alone at 26-29 % softirq) against **203/235/180/227 Mbit/s with RPS on** — **+60 %, for one sysfs write**. `/etc/ppp/ip-up.d/nivuus-rps-ecores` (repo: `configs/network/ppp-ip-up.d/`) runs `/usr/local/bin/net-rps-ecores.sh apply ppp0` at every link-up, because ppp0 is destroyed and recreated with `rps_cpus` back to 0 on each reconnection — a boot-time one-shot would silently stop applying after the first Orange cooldown. The script picks the E-cores by their lower max frequency, never hard-coded (this box has no `/sys/devices/system/cpu/types` to ask); tests in `scripts/tests/test_net_rps_ecores.sh`. **RPS cannot split a single TCP flow across cores** (it would reorder packets): the gain comes from moving the TCP/IP half off the core doing PPPoE decapsulation, so a mono-flow download still has one hot core — it is just no longer doing all the work alone.

**Every IRQ is banned from the P-cores (2026-08-26).** `IRQBALANCE_BANNED_CPULIST=0-15` in `/etc/default/irqbalance` (repo: `configs/cpu/irqbalance-default.conf`; original kept as `/etc/default/irqbalance.avant-ban-pcores`). irqbalance had piled **98 IRQ onto cpu0 alone** — ahci (304 M interrupts), the three `enp5s0` queues (420 M together), xhci (34 M), the nvme admin queue — and cpu0 is pinned by the Windows VM whenever it runs, so every one of those fired straight into a live vCPU while gaming. The cpuset partitioning in `vm-cpu-partition.sh` never covered this: **IRQ are not subject to cgroups**, which is exactly why the hole went unnoticed. After the ban: **0 IRQ left on cpu0-15, 205 on the E-cores**, NVMe direct read unchanged at 1.6 GB/s, no failed unit. Kernel-managed IRQ (nvme io queues) are out of irqbalance's reach either way and the kernel already spreads them.

**Recovery if internet is down at boot:** `sudo nmcli connection up pppoe-enp6s0.835` (after a ~5 min wait if Orange is in cooldown). Last-resort fallback: `sudo systemctl start pppoe-dsl.service` (disable NM autoconnect first to avoid a fight: `nmcli con modify pppoe-enp6s0.835 connection.autoconnect no`). The bridges/WiFi (localBridge/publicBridge/internalBridge) are NM-managed and rebuild cleanly on a full reboot — a NM *restart* (not reboot) can orphan localBridge's members (hostapd wlp10s0/wlp11s0 + enp14s0), breaking home WiFi; a reboot fixes it.

## Related Documentation

- **Main README**: `/README.md` - Project overview and installation
- **Installer**: `/installer/README.md` - ISO build, portal, install engine
- **System Audit**: `/docs/system-audit.md` - Complete infrastructure documentation
- **Network Config**: `/configs/network/` - NetworkManager and hostapd setup
- **Firewall Config**: `/configs/firewall/` - firewalld and nftables rules
- **VM Config**: `/docs/vm-configuration.md` - QEMU/KVM setup with GPU passthrough
- **Specs & plans**: `/docs/superpowers/` - design documents and implementation plans
- **MQTT agent**: now `nivuus/mqtt` - its CLAUDE.md holds the agent-side documentation
