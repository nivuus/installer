# console — la console de jeu Windows, en package Nivuus

This directory is a **Nivuus package** (`nivuus.dev/v1`): the installer
engine discovers it, offers it in the wizard, and installs it through the
same three phases any third-party package goes through. It is not special —
that is the point. If the API were not enough for this, it would not be
enough for anyone.

| Phase | What it does |
|---|---|
| `resolve` | Read-only. Derives `vfio-pci.ids` from the discrete GPU's PCI slot and the dedicated NVMe, `nohz_full` from the CPU topology, and the hugepage budget from host RAM. **Refuses**, with a reason, a machine with no discrete GPU or no properly isolated NVMe. |
| `install` | Places files on the target — exactly the list below, no more. |
| `activate` | Arms four of the eight systemd units `install` placed (the two wake sockets, the idle-shutdown timer, the guest-readiness timer) with a symlink into their `.wants/` directory, then reloads systemd and starts them. Then runs five steps to build and start the Windows guest — write the three secrets, fetch the offline payload, build the unattended ISO, define the libvirt domain with both install media, and issue one `virsh start` — each skippable when its own observation says it is already done. It **returns as soon as the VM has been told to start**; Windows Setup itself runs unattended for up to an hour afterwards, and only `nivuus-guest-ready.timer` says how it went. See below. |

## What `install` actually deploys

The full list, because a package README that over-promises is what the next
package author builds against:

| Placed on the target | Where |
|---|---|
| `host/libvirt/hooks/qemu`, the dispatcher | `/etc/libvirt/hooks/qemu` |
| `host/vm-cpu-partition.sh` | `/etc/libvirt/hooks/vm-cpu-partition.sh` |
| `host/libvirt/hooks/qemu.d/Windows/prepare/begin/bind-vfio-gpu.sh` | `/etc/libvirt/hooks/qemu.d/Windows/prepare/begin/bind-vfio-gpu.sh` |
| `host/libvirt/hooks/qemu.d/Windows/release/end/rebind-host-gpu.sh` | `/etc/libvirt/hooks/qemu.d/Windows/release/end/rebind-host-gpu.sh` |
| `host/libvirt/hooks/qemu.d/Windows/started/begin/rules.sh` | `/etc/libvirt/hooks/qemu.d/Windows/started/begin/rules.sh` |
| `host/libvirt/hooks/qemu.d/Windows/stopped/end/rules.sh` | `/etc/libvirt/hooks/qemu.d/Windows/stopped/end/rules.sh` |
| `host/libvirt/hooks/qemu.d/Windows/prepare/begin/10-cpu-confine.sh` | `/etc/libvirt/hooks/qemu.d/Windows/prepare/begin/10-cpu-confine.sh` |
| `host/libvirt/hooks/qemu.d/Windows/release/end/10-cpu-release.sh` | `/etc/libvirt/hooks/qemu.d/Windows/release/end/10-cpu-release.sh` |
| `host/vm-wake-gate.py` | `/usr/local/sbin/vm-wake-gate.py` |
| `host/handle-vm-start.sh` | `/usr/local/sbin/handle-vm-start.sh` |
| `host/vm-idle-shutdown.sh` | `/usr/local/sbin/vm-idle-shutdown.sh` |
| `host/winvm` | `/usr/local/bin/winvm` |
| `host/guest-ready-watch.py` | `/usr/local/sbin/guest-ready-watch.py` |
| `vm-trigger-47984.socket` + `.service`, `vm-trigger-47989.socket` + `.service`, `vm-idle-shutdown.service` + `.timer`, `nivuus-guest-ready.service` + `.timer` (8 units) | `/etc/systemd/system/` |
| the shared no-start-limit drop-in, copied twice | `/etc/systemd/system/vm-trigger-{47984,47989}.service.d/no-start-limit.conf` |
| the retrogaming answer | `/etc/nivuus/retro.json` |

The two CPU wrappers are **copied from the repository, not generated**. They
used to be heredocs inside `install.py` that called `vm-cpu-partition.sh` and
stopped there, dropping `systemctl start nivuus-cpu-mode@{gaming,idle}.service`
— a contract this repository declares publicly and deploys the host half of
(`install-engine/steps/features.py`), and which no code honoured while the
heredocs were what landed: a console started its VM without ever switching to
the gaming CPU policy.

`vm-cpu-partition.sh` lands under `/etc/libvirt/hooks/` and nowhere else —
the libvirtd AppArmor profile grants `/etc/libvirt/hooks/** rmix` but not
`/usr/local/sbin/*`, so a copy placed there dies at VM start with a
misleading `bad interpreter: Permission denied` and no DENIED line in dmesg.

The eight units are placed by `install` at mode `0644` and **not enabled** —
arming a `0.0.0.0` wake socket for a VM that does not exist yet would be
exposure with no counterpart. `activate` arms four of them afterwards (the
two wake sockets, the idle-shutdown timer, and the guest-readiness timer)
with a symlink into their `.wants/` directory; the two `vm-trigger-*.service`
units, `vm-idle-shutdown.service` and `nivuus-guest-ready.service` are never
enabled directly, they run via socket and timer activation.

The link is what makes the **next boot** correct; `activate` also reloads
systemd and starts the four units, because the unit that runs it is
`WantedBy=multi-user.target` and therefore fires *after* `sockets.target` and
`timers.target` have been reached — without the explicit start, the wake
sockets would not listen and the timer would not tick until a second reboot,
while the activation stamp already claimed success. That start is best-effort
(a failure is reported and the next boot still arms everything), and it is
skipped entirely when `--root` points somewhere other than `/`: driving the
installer's own systemd from a target root would be the wrong machine.

## What `activate` does to build the guest (phase 2d)

The libvirt hooks, the wake path, and the host scripts are all wired and
armed — with the host-specific constants listed under **Limites connues**
below. Beyond arming the four units above, `activate` runs
`console/guest_steps.py`'s five steps in order, each guarded by its own
`already_done()` predicate so a re-run after a partial failure only replays
what did not finish:

1. **secrets** — write the three `0600` files (`windows-ltsc.key`,
   `windows-admin.pass`, `apollo-ui.pass`) `guest/build.py` reads its secrets
   from, never on argv.
2. **payload** — `guest/fetch_payload.py` fetches the offline driver/tool
   binaries.
3. **build** — `guest/build.py` renders the unattended ISO (answer file +
   payload), fingerprinted (medium identity, payload tree, answers, package
   code) so any change — not just a date — forces a rebuild.
4. **define** — `guest/domain.py define` declares the libvirt domain with
   **both** install media attached: the official Windows medium (the one
   that boots) and the ISO `build` just produced (the answer/payload medium
   Setup reads once running — not itself bootable). `--replace` is added
   only when a domain is already defined, never blindly.
5. **start** — one `virsh start Windows`.

**`activate` returns as soon as `start` succeeds — it does not wait for
Windows Setup to finish.** That install runs unattended for up to an hour.
A separate mechanism says how it went: `nivuus-guest-ready.timer` (armed
above, 2-minute period, self-stopping) polls `virsh domstate` and a plain
TCP connect to WinRM (5985) — the same port `provision/99-marker.ps1` opens
only once the other thirteen provisioning stages have already succeeded —
and logs one of four states to the journal: `not_started`, `installing`,
`failed` (past a 2h timeout with the port still closed), or `ready`. On
`ready` it also redefines the domain **without** either install medium
(`domain.py define --replace --keyed-varstore`), so the next boot does not
risk re-running Setup; the timer only stops itself once that redefinition
has actually succeeded, not merely on `ready`, so a transient failure there
is retried rather than leaving the media attached forever.

**This chain has never run for real.** Every step is proven individually
against a fake `virsh`/filesystem (`test_console_guest_steps`) and the
readiness classification is proven the same way
(`test_console_guest_ready`) — but nothing here has actually built an ISO,
defined the production domain, or started it, deliberately: on the
reference host `Windows` is the production gaming VM, and starting it
detaches the GPU from the host.

Known gaps in this chain, named rather than hidden:

- **`--replace` on the `define` step is a real risk on a machine that
  already has a production VM.** `domain.py`'s `guard_replace()` still
  refuses to redefine an existing domain without `--replace`, and the step
  only adds it when one is already defined — but redefining `Windows` on a
  host where it is already the owner's VM discards a hibernated session.
  The guard is a backstop against an *accidental* redefinition, not a
  substitute for an operator choosing not to run this against that machine.
- **The first reboot inside Setup is unmeasured.** The reasoning holds — the
  test bench ran two full unattended LTSC installs with this exact shape of
  domain (both media attached, then redefined without them) — but the only
  thing standing between a normal install and a boot loop is the "Press any
  key to boot from CD or DVD" prompt on the operator-supplied medium itself,
  and the bench has never installed onto an NVMe passed through as a PCI
  device, only onto a disk image.
- **A `windows_iso` answer given as a URL is not fetched.** `media_identity()`
  only ever `stat()`s a local path; nothing in this chain downloads a
  multi-gigabyte ISO, which would need resume, integrity checking and free
  space accounting this phase did not build. Point it at a path that already
  exists on the target — see the wizard table below.
- **The `payload` step's own "already done" check is shallow**: a directory
  with at least one file in it counts as done, even if `fetch_payload.py`
  was interrupted mid-fetch. A partial payload is not caught here — it is
  caught, and repaired, by the `build` step re-running against it.

## The wizard's questions

Seven answers total; the first three predate this phase, the last four are
what it took to actually build and boot the guest:

| Key | Type | What it drives |
|---|---|---|
| `dedicated_nvme` | disque | the PCI device handed whole to the VM |
| `retro` | bool | whether `fetch_payload.py`/`build.py` stage RetroArch |
| `admin_password` | secret | the guest Administrator's password |
| `windows_iso` | texte | the official Windows medium — a **local path only** (see above); the wizard's own label still reads "chemin local ou URL", ahead of the fetch this phase does not implement |
| `ltsc_key` | secret | the LTSC product key baked into the answer file |
| `apollo_password` | secret | the Apollo streaming UI's password |
| `guest_workdir` | texte | where secrets/payload/the built ISO live, default `/var/lib/nivuus/guest` |

## Limites connues

The host-side scripts were brought into the repository **verbatim** from the
production host, deliberately: a faithful copy first, parameterisation second
— rewriting them while moving them would have made any regression
indistinguishable from a transcription error. What that costs today, checked
file by file:

- **The GPU's PCI address is hard-coded.** `rebind-host-gpu.sh` carries
  `GPU_PCI=0000:01:00.0` while `resolve` already knows the real slot (it reads
  it from the hardware snapshot and derives `vfio-pci.ids` from it) and
  nothing substitutes it. On a machine whose GPU sits elsewhere, the rebind
  loop simply times out with a `WARNING` — the host silently never gets its
  card back.
- **The two halves of the wake path disagree about the VM's bridge.**
  `handle-vm-start.sh` sets `VM_INTERFACE="localBridge"` (still annotated
  `<<< REMPLACEZ CECI`), which the engine gives `192.168.0.1/24`, while the
  rest of the chain pins the VM to `192.168.3.2` — `internalBridge`
  (`MANAGED_VM_IP` in both `rules.sh`, `VM_IP` in `vm-idle-shutdown.sh`,
  `VM_HOSTNAME` in `winvm`). The address wait therefore looks on the wrong
  bridge.
- **Hooks reference `/opt/nivuus/…` paths that do not exist on a fresh
  target.** `bind-vfio-gpu.sh` and `rebind-host-gpu.sh` drive
  `docker compose -f /opt/nivuus/{ollama,media-manager}/docker-compose.yml`,
  the two CPU wrappers stop and restart the Tdarr CPU node from the same
  place, and `vm-idle-shutdown.sh` tries to bring ollama up on every idle
  cycle. All of these are guarded (`|| true`, or their failure is ignored),
  so they are noise rather than breakage.
- **`winvm` is deployed without the client it needs.** It requires
  `/usr/local/bin/winrm`, and `console/host/install-winrm-cli.sh` — the script
  that installs it — is placed by nobody; the password it reads from
  `~/.config/nivuus/winvm.conf` is created by nobody either. So the hibernation
  call in `vm-idle-shutdown.sh` always fails, the observation loop runs its 90
  seconds for nothing, and the VM falls back to the **ACPI shutdown** on the
  line below — the session is lost instead of being suspended, which is not
  what the rest of this documentation describes.

Parameterising these constants from `resolve`'s output is the next phase's
work, not this one's.

## PCI passthrough only

The dedicated disk is handed over as a **whole PCI device**, never as a disk
image or a virtio block device. A machine whose NVMe cannot be detached from
the host is refused in `resolve` rather than silently downgraded — a console
that boots but performs like a laptop is worse than one that says why it
cannot be installed.

**The operator's `dedicated_nvme` answer chooses the device.** The host-root
exclusion is then an assertion on that choice, not the way the choice is
made. That direction is load-bearing: on the installer ISO — the package's
primary path — the host root is the live image, no PCI disk backs it, and a
selector deriving from it can only ever refuse. The engine adds the one
check the package cannot make: it refuses an answer naming the **install
target**, because a hook never sees the install config.

## Package structure

Beyond `hooks/` and `host/`, the package also carries:

| Directory | What it holds |
|---|---|
| `guest/` | The unattended LTSC build, the libvirt domain generator (`domain.py`), the retrogaming sync, and the provisioning scripts — 41 files, moved here from `installer/windows-guest/` so the package is self-contained. `activate` now drives `fetch_payload.py`, `build.py` and `domain.py` through `guest_steps.py` (see above). |
| `tests/` | The package's own test suites — 22 files (21 Python plus the shell suite `test_handle_vm_start.sh`), all run by `console/Makefile`'s `test` target, which `installer/Makefile`'s `test-packages` delegates to. |

No file under `console/` **source** imports from `installer/common` or
anywhere else in `installer/` — the package is self-contained, which is what
makes a future `git filter-repo --path console` mechanical rather than a
rewrite. The boundary does not (yet) hold for the tests: `tests/test_console_resolve.py`
adds `installer/` to `sys.path` and imports `packages.manifest`/`packages.runner`/
`packages.wizard` to exercise `resolve` through the real engine contract —
a dated blocker for a future extraction, not a source-code exception.
