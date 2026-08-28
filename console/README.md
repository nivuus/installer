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
| `activate` | Arms three of the six systemd units `install` placed (the two wake sockets, the idle-shutdown timer) with a symlink into their `.wants/` directory, then reloads systemd and starts them. It does **not** build the guest: the Windows VM is still made by hand with `console/guest/build.py` (phase 2d). |

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
| `vm-trigger-47984.socket` + `.service`, `vm-trigger-47989.socket` + `.service`, `vm-idle-shutdown.service` + `.timer` (6 units) | `/etc/systemd/system/` |
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

The six units are placed by `install` at mode `0644` and **not enabled** —
arming a `0.0.0.0` wake socket for a VM that does not exist yet would be
exposure with no counterpart. `activate` arms three of them afterwards (the
two wake sockets and the idle-shutdown timer) with a symlink into their
`.wants/` directory; the two `vm-trigger-*.service` units and
`vm-idle-shutdown.service` are never enabled directly, they run via socket
and timer activation.

The link is what makes the **next boot** correct; `activate` also reloads
systemd and starts the three units, because the unit that runs it is
`WantedBy=multi-user.target` and therefore fires *after* `sockets.target` and
`timers.target` have been reached — without the explicit start, the wake
sockets would not listen and the timer would not tick until a second reboot,
while the activation stamp already claimed success. That start is best-effort
(a failure is reported and the next boot still arms everything), and it is
skipped entirely when `--root` points somewhere other than `/`: driving the
installer's own systemd from a target root would be the wrong machine.

## What `install` does NOT deploy yet (phase 2d)

The libvirt hooks, the wake path, and the host scripts are all wired and
armed — with the host-specific constants listed under **Limites connues**
below. What is left is the Windows guest itself: `activate` does not build
it. The console can manage a `Windows` domain **if one already exists** —
it cannot create one. `console/guest/build.py` + `domain.py` already live
in this package (moved from `installer/windows-guest/` in an earlier
phase) and `domain.py` produces real domain XML on this hardware — but
both are still run by hand. Wiring `activate` to drive them is phase 2d's
work, not this one's.

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
  `docker compose -f /opt/nivuus/{ollama,MediaManager}/docker-compose.yml`,
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
| `guest/` | The unattended LTSC build, the libvirt domain generator (`domain.py`), the retrogaming sync, and the provisioning scripts — 41 files, moved here from `installer/windows-guest/` so the package is self-contained. `activate` does not drive any of it yet (see above). |
| `tests/` | The package's own test suites — 20 files (19 Python plus the shell suite `test_handle_vm_start.sh`), all run by `console/Makefile`'s `test` target, which `installer/Makefile`'s `test-packages` delegates to. |

No file under `console/` imports from `installer/common` or anywhere else
in `installer/` — the package is self-contained, which is what makes a
future `git filter-repo --path console` mechanical rather than a rewrite.
