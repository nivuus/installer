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
| `activate` | Arms three of the six systemd units `install` placed (the two wake sockets, the idle-shutdown timer) with a symlink into their `.wants/` directory. It does **not** build the guest: the Windows VM is still made by hand with `installer/windows-guest/build.py` (phase 2c). |

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
| the CPU-confine wrapper (written inline) | `/etc/libvirt/hooks/qemu.d/Windows/prepare/begin/10-cpu-confine.sh` |
| the CPU-release wrapper (written inline) | `/etc/libvirt/hooks/qemu.d/Windows/release/end/10-cpu-release.sh` |
| `host/vm-wake-gate.py` | `/usr/local/sbin/vm-wake-gate.py` |
| `host/handle-vm-start.sh` | `/usr/local/sbin/handle-vm-start.sh` |
| `host/vm-idle-shutdown.sh` | `/usr/local/sbin/vm-idle-shutdown.sh` |
| `host/winvm` | `/usr/local/bin/winvm` |
| `vm-trigger-47984.socket` + `.service`, `vm-trigger-47989.socket` + `.service`, `vm-idle-shutdown.service` + `.timer` (6 units) | `/etc/systemd/system/` |
| the shared no-start-limit drop-in, copied twice | `/etc/systemd/system/vm-trigger-{47984,47989}.service.d/no-start-limit.conf` |
| the retrogaming answer | `/etc/nivuus/retro.json` |

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

## What `install` does NOT deploy yet (phase 2c)

The libvirt hooks, the wake path, and the host scripts are all wired and
armed. What is left is the Windows guest itself: `activate` does not build
it. The console can manage a `Windows` domain **if one already exists** —
it cannot create one. That is `installer/windows-guest/build.py` +
`domain.py`, run by hand today, folded into this package in phase 2c.

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

## What is not here yet

`installer/windows-guest/` (the unattended LTSC build, the libvirt domain
generator, the provisioning scripts) still lives outside this directory. It
moves in phase 2c, after which this package is self-contained and phase 3
is a `git filter-repo --path console` away.
