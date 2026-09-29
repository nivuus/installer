# Installer: the console package and its host-side lifecycle

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
  the two versioned CPU wrappers (copied, never generated: the heredocs they
  replaced dropped `systemctl start nivuus-cpu-mode@{gaming,idle}.service`,
  which this file calls a public contract two paragraphs up, leaving it
  honoured by nobody), four host scripts (`vm-wake-gate.py`,
  `handle-vm-start.sh`, `winvm`, and `vm-idle-shutdown.sh`), six systemd
  units plus their shared no-start-limit drop-in copied into two `.d/`
  directories, and `retro.json`. `console/hooks/activate.py` is no longer a
  stub: it arms three of those six units (the two wake sockets and the
  idle-shutdown timer) with a symlink into their `.wants/` directory, then
  — only when `--root` is `/` — reloads systemd and starts them, tolerating
  failure. **That start is not a nicety**: the activation unit is
  `WantedBy=multi-user.target`, so it runs after `sockets.target` and
  `timers.target` are already reached; the links alone would leave the wake
  sockets silent and the timer stopped until a *second* reboot, with the
  stamp file already claiming success. The
  three hugepage hooks (`00-set-hugepages.sh`, `00-hugepages-fix.sh`,
  `hugepages-reset.sh`) were **deleted**, not deployed — the hugepage pool is
  already set declaratively: `console/hooks/resolve.py` computes it from host
  RAM and returns it as the `platform` event's `hugepages-mib` (the manifest
  declares no such key), and `install-engine/steps/packages.py` writes
  `vm.nr_hugepages` into `/etc/sysctl.d/60-nivuus-packages.conf`. So
  deploying them would have done nothing; documenting
  them as "to be wired" had already misled the README once. The one thing
  still missing: **`vm-idle-shutdown.sh` was never versioned anywhere before
  this plan** — same class of gap as `handle-vm-start.sh` before
  2026-08-24, a script that existed only deployed on the production host.
  Both are now source in `console/host/`. **At that point (tasks 1-4) the
  console was still not functional from an install alone**: `activate`
  armed only the wake/idle units and could manage a `Windows` domain if one
  already existed, not create one.

* **The console runs an anti-detection QEMU, not Debian's (2026-09-15, owner's
  decision, reversing the 2026-08-22 framing that refused hypervisor masking).**
  `console/host/qemu-anti-detection/build-qemu.sh` builds upstream QEMU 10.2.2
  with the vendored zhaodice/qemu-anti-detection patch into
  `/opt/qemu-anti-detection` (never `/usr/local`, which would shadow the
  packaged binary for libvirt's own probing); `console/qemu_build.py` is the
  Python side (prefix, stamp `qemu=<ver> patch=<sha256>`, predicate) and wires
  it as the `qemu` step of `guest_steps.plan_steps`, **before `define`**,
  because libvirt validates the `<emulator>` binary at define time. The
  generated domain carries `<kvm><hidden/>`, `hypervisor` disabled and a
  `vendor_id`, with the host's own SMBIOS. Three traps, all measured on the
  production host that day: (1) **two AppArmor site files are needed, not
  one** — libvirtd itself probes the binary as `libvirt-qemu` and its profile
  execs `/usr/bin/*` only, so `virsh define` dies with *Failed to probe QEMU
  binary … Permission denied* and no DENIED line; `local/usr.sbin.libvirtd`
  (`PUx`; a live host needs `apparmor_parser -r`, which `activate` runs
  on every activation of the running machine) covers that, and
  `local/abstractions/libvirt-qemu` (`rmix` + the datadir `r`) covers the VM
  itself; (2) **the upstream patch renames the virtio PCI vendor 0x1af4 →
  0x8086** and the virtio-win drivers then never bind: the guest boots and
  answers ACPI but its NIC sends nothing and the virtiofs shares vanish — the
  vendored patch drops that hunk, a test in `test_console_host_files.py`
  keeps it dropped, and `host/qemu-anti-detection/README.md` records the
  provenance (upstream commit `2750c86`, 2026-04-18); (3) the emulated VGA
  keeps showing the boot logo once Windows moves to the NVIDIA display, so a
  `virsh screenshot` proves nothing about whether the guest is up — the tap
  interface's `rx_packets` and an ACPI shutdown answered in seconds do.
  Build deps are declared in `nivuus-package.yaml`'s `apt` list; the build
  takes ~8 min on this host and is skipped on every later activation.

* **`activate` now builds and starts the guest too — phase 2d, plan
  `2026-08-28-console-activate-invite`, done 2026-08-28.** `install` places
  two more units (`nivuus-guest-ready.service`+`.timer`, eight total) and a
  new script, `guest-ready-watch.py`; `activate` arms four of the eight
  (adding the guest-readiness timer to the three above), then runs
  `console/guest_steps.py`'s five ordered steps — write the three 0600
  secrets, fetch the offline payload, build the unattended ISO, `domain.py
  define` with **both** install media (the official Windows medium, which
  boots, plus the just-built answer ISO, which does not), and one `virsh
  start` — each skippable on its own `already_done()` observation, and each
  failure classified into one of three causes (a refused input, a libvirt
  hook refusal at `start`, or an unnamed command failure) by
  `console/hooks/activate.py`'s `classify()`. **`activate` returns as soon
  as `start` succeeds — it does not wait for Windows Setup to finish.**
  `nivuus-guest-ready.timer` (2-minute period, self-stopping) is what says
  the real outcome: it polls `virsh domstate` and reads a version-stamped
  marker file over WinRM (`C:\nivuus\state\PROVISION.done`, written by
  `provision/99-marker.ps1` as its last act, checked against `payload.py`'s
  `PROVISION_VERSION` so a rebuilt disk's PREVIOUS run's marker never reads
  as ready). **A reachable WinRM port (5985) is NOT the readiness signal —
  this file said so until 2026-08-28, and that stale claim caused the whole
  redesign this bullet documents**: since 2026-08-26 `provision/00-bootstrap.ps1`
  opens 5985 at the FIRST provisioning stage, deliberately, so a reachable
  port only ever proves the guest is alive enough to accept a command, never
  that provisioning finished. The timer logs
  `not_started`/`installing`/`failed` (2h timeout)/`ready`. On `ready` it
  redefines the domain **without** either install medium
  (`domain.py define --replace --keyed-varstore`), stopping the timer only
  once that redefinition itself has actually succeeded, so a transient
  failure there is retried rather than leaving the media attached forever.
  `console/guest/` and `domain.py` are no longer merely "already inside the
  package" — `activate` now drives both.
  **Named residuals, not silently accepted**: (1) `--replace` on `define`
  is a real risk on a host where `Windows` is already the production VM —
  `guard_replace()`/`guard_fresh_varstore()` remain the backstop, not a
  substitute for an operator's own judgment; (2) the first reboot inside
  Setup on this exact domain shape (both media, then redefined without
  them) is unmeasured — the bench ran two full LTSC installs this way, but
  the only guard against a boot loop is the "Press any key" prompt on the
  operator's own medium, and the bench never installed onto an NVMe passed
  through as PCI; (3) a `windows_iso` answer given as a URL is never
  fetched — `media_identity()` only `stat()`s a local path, the wizard's own
  label still says otherwise, and downloading 5 GB with resume/integrity
  was deliberately left out of scope; (4) the `payload` step's
  `already_done()` is shallow — an interrupted fetch still counts as done,
  and it is the `build` step re-running against it that repairs the gap;
  (5) `retro: true` cannot succeed on a target even though the wizard
  offers it — `fetch_payload.py`'s `RETRO_SRC` default resolves to
  `/opt/retro` once this package is copied to `/opt/nivuus-packages/console/`,
  and nothing creates that directory there, so `build_retro_wheels()`
  refuses by name the moment `payload` runs with retro on; (6) the WinRM
  password-file path `guest-ready-watch.py` passes to `winrm_exec.py`
  (`GUEST_PASS_FILE`) is deduced from reading `guest_steps.py`'s own
  `secrets` step side by side with it, never measured against a real guest
  — running one is exactly what this whole chain is forbidden from doing.
  **None of this chain has ever run for real** — every step is proven
  against a fake `virsh`/filesystem, never on the reference host, because
  starting `Windows` there detaches the GPU from the host. The host-specific
  constants this paragraph does NOT re-litigate (hard-coded GPU PCI address,
  the wake path's wrong bridge, `/opt/nivuus/…` paths, the missing `winrm`
  client) are unchanged and stay documented in `console/README.md`'s
  **Limites connues** — a separate, still-open item.
