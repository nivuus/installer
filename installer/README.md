# Nivuus Installer

A bootable ISO that installs Nivuus from a **web wizard served over a WiFi
hotspot**. Boot the USB stick on the target machine, connect a phone/laptop to
the `Nivuus-Setup-XXXX` WiFi (password shown on the server console), and the
configuration page opens automatically. Pick the disk, network and features,
press *Installer*, then reboot into a configured Nivuus server.

## How it works

```
 live boot (RAM)
   │
   ├─ nivuus-ap.service   → bring-up-ap.sh
   │      detects an AP-capable WiFi card, starts hostapd + dnsmasq on
   │      10.42.0.1/24 with a captive DNS. No WiFi? → Ethernet DHCP fallback.
   │
   └─ nivuus-portal.service → webapp/main.py (FastAPI on :80)
          serves the wizard, detects hardware, and on submit spawns…
                │
                └─ install-engine/run.py
                       partition → debootstrap → base config → kernel/GRUB →
                       apply Nivuus features (thermal policy, an ordinary
                       install-engine feature) → apply selected packages
                       (e.g. `console`, the Windows gaming guest) →
                       validate. Progress streams back over a WebSocket.
```

The live root runs entirely in RAM; the engine debootstraps Debian onto the
**target disk** — the live image is never written to disk.

## Layout

| Path | Role |
|------|------|
| `common/hardware.py` | Generic hardware detection (disks, NICs, WiFi AP-capability, coarse GPU/IOMMU/NVMe capabilities). Precise details (`vfio-pci.ids`, `isolcpus`/`nohz_full`) are a package's job — see `console/hardware.py` for the reference. |
| `common/progress.py` | Structured progress-event protocol (jsonl log + stdout) shared by engine and portal. |
| `install-engine/run.py` | Orchestrator: scripted debootstrap install, emits progress. |
| `install-engine/steps/` | `partition`, `debootstrap`, `chroot_base`, `bootloader`, `features`, `validate`. |
| `install-engine/templates/` | Jinja2 configs: NM bridges, VLAN, PPPoE, hostapd. |
| `webapp/` | FastAPI portal: `main.py` (routes + `/ws/progress`), `models.py` (Pydantic), `installer_runner.py`, `static/` + `templates/` wizard. |
| `ap/` | Hotspot bring-up: `bring-up-ap.sh`, `hostapd-setup.conf.tmpl`. |
| `iso-build/` | live-build config, hooks (venv + enable services), `build.sh`. |

The installer **reuses the repo's own scripts** rather than duplicating logic:
`scripts/optimize-cpu-thermal.sh` (deployed by the thermal `install-engine`
feature), `scripts/validate-install.sh`. The whole repo is copied into the
target at `/opt/nivuus`. `install.sh` is gone (2026-08-27): the VM-setup
blocks it used to run became the `console` package (below), and its thermal
block became the fifteen-line `_thermal()` feature in
`install-engine/steps/features.py`.

## Packages

A **package** extends the installer with host-side features it does not ship
itself. It is a directory carrying a `nivuus-package.yaml`, discovered at
install time under `/opt/nivuus-packages/*/`, and embedded into the ISO from a
sibling repository:

```bash
PACKAGE_REPOS="$HOME/Projects/Nivuus/packages/console" sudo -E make build-iso
```

### `console`, the first package

`console/` in this repository is the reference consumer of this API: the
Windows gaming guest, installed through exactly the three phases a
third-party package goes through. It is deliberately not privileged — if the
contract were not enough for it, it would not be enough for anyone.

```bash
PACKAGE_REPOS="$PWD/console" sudo -E make build-iso
```

Its `resolve` phase refuses, with a reason, any machine with no discrete GPU
or no properly isolated NVMe: the console is PCI-passthrough only, and a
silent fallback to a disk image would deliver something slower than what was
asked for.

### `media-manager`, the first out-of-tree package

`~/Projects/Nivuus/packages/media-manager` is the first package living
**outside this repository**: fifteen containers (Plex, the *arr suite, Tdarr,
Bazarr) deposited into `/opt/nivuus/media-manager` and started on first boot.

```bash
PACKAGE_REPOS="$HOME/Projects/Nivuus/packages/media-manager" sudo -E make build-iso
```

It has no `resolve`, no `claims` and no `requires`: `tier: userspace`, two
phases, and its dependencies declared in `apt:`. That is the demonstration
that the contract is enough for a package which does not touch the boot chain
— and its lack of a GPU claim is what keeps it co-installable with `console`,
whose libvirt hooks share the card with its NVENC transcoding node.

Both packages embed together, separated by a space:

```bash
PACKAGE_REPOS="$PWD/console $HOME/Projects/Nivuus/packages/media-manager" \
  sudo -E make build-iso
```

### Three phases, named relative to the reboot

| Phase | When | Receives | May |
|---|---|---|---|
| `resolve` | Before any write | `hw` + wizard answers on stdin | **Read only.** Return the resolved platform block, or refuse with a reason |
| `install` | On a target filesystem | `--root` (`/mnt/target` in the ISO, `/` standalone) | Write under that root |
| `activate` | After the reboot, network up | — | Anything |

A hook reads a JSON context on **stdin** and emits jsonl events on **stdout**:
`{"event":"progress","pct":N,"msg":"…"}`, `{"event":"platform","kernel-cmdline":[…],"modules":[…],"hugepages-mib":N}`,
`{"event":"refuse","reason":"…"}`, `{"event":"done"}`. A non-zero exit fails the install.

### Two tiers

`userspace` may declare `apt`, questions and hooks. `platform` may additionally
declare `kernel-cmdline`, `modules` and `hugepages-mib` — and the wizard then
shows the resolved kernel command line verbatim and asks for its own
confirmation. A `userspace` manifest declaring any of the three is **refused**,
not silently stripped.

See `scripts/tests/fixtures/packages/demo/` for a complete working example.

### How `activate` reaches the installed system

Nothing about the live medium survives the reboot, so the install step puts
all three moving parts on the target itself:

| On the target | Copied from | By |
|---|---|---|
| `/etc/systemd/system/nivuus-package-activate@.service` | `/opt/nivuus/configs/systemd/` (the payload) | `apply_packages()` |
| `/opt/nivuus/installer/packages/activate_cli.py` | the repo payload | `copy_payload()`, made executable by `apply_packages()` |
| `/opt/nivuus-packages/<name>/` | the live medium, **selected packages only** | `apply_packages()` |

The unit's `ExecStart` points at `activate_cli.py` **where the payload already
puts it** rather than at a copy under `/usr/local/sbin`: the script derives its
own `sys.path` from `__file__`, so moving it would break its imports.

Activation is armed by creating
`etc/systemd/system/multi-user.target.wants/nivuus-package-activate@<name>.service`
directly — that is exactly what `systemctl enable` does for a template unit
with `WantedBy=multi-user.target`, without needing a working `systemctl`
inside the chroot. Any of these failing fails the install: an install that
reports success while first-boot activation cannot run is the failure mode
this replaced.

## Build the ISO

```bash
sudo apt-get install live-build        # build host (root)
cd installer
sudo make build-iso                    # → iso-build/*.iso
# include the prebuilt MQTT .deb: BUILD_MQTT_DEB=1 sudo -E make build-iso
```

## Test without rebuilding the ISO

```bash
# Web portal locally on :8080 (uses this machine's real hardware in the wizard)
make test-portal

# Engine against a loopback disk image (root) — safe, no real disk touched
truncate -s 16G /tmp/t.img
LOOP=$(sudo losetup --find --show /tmp/t.img)
# edit config.json's disk.path to $LOOP, then:
sudo NIVUUS_PROGRESS_DIR=/tmp/p python3 install-engine/run.py \
     --config config.json --target /tmp/mnt --nivuus-src .. --stop-after partition
sudo losetup -d $LOOP

# Boot the built ISO in QEMU (UEFI); portal reachable at http://localhost:8080
make test-vm
```

`--stop-after {partition,debootstrap,base,bootloader,features}` halts the engine
early for staged testing.

## Notes / limitations

- **WiFi AP can't be emulated in QEMU**; test the hotspot on real hardware via a
  USB stick. In the VM the portal is reached over the Ethernet fallback.
- The portal binds port 80 and runs the engine as root — it is meant to run only
  inside the throwaway live environment.
- Bookworm ships pydantic v1; the portal needs v2, so the live image builds a
  Python venv from `webapp/requirements.txt` (hook `0500-nivuus-venv`).
