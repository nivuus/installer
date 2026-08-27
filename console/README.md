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
| `activate` | **Not implemented yet** (phase 2b). The Windows guest is still built by hand with `installer/windows-guest/build.py`. |

## What `install` actually deploys

The full list, because a package README that over-promises is what the next
package author builds against:

| Placed on the target | Where |
|---|---|
| `host/vm-cpu-partition.sh` | `/etc/libvirt/hooks/vm-cpu-partition.sh` |
| the CPU-confine wrapper (written inline) | `/etc/libvirt/hooks/qemu.d/Windows/prepare/begin/10-cpu-confine.sh` |
| the CPU-release wrapper (written inline) | `/etc/libvirt/hooks/qemu.d/Windows/release/end/10-cpu-release.sh` |
| `host/vm-wake-gate.py` | `/usr/local/sbin/vm-wake-gate.py` |
| `host/handle-vm-start.sh` | `/usr/local/sbin/handle-vm-start.sh` |
| `host/winvm` | `/usr/local/bin/winvm` |
| the retrogaming answer | `/etc/nivuus/retro.json` |

`vm-cpu-partition.sh` lands under `/etc/libvirt/hooks/` and nowhere else —
the libvirtd AppArmor profile grants `/etc/libvirt/hooks/** rmix` but not
`/usr/local/sbin/*`, so a copy placed there dies at VM start with a
misleading `bad interpreter: Permission denied` and no DENIED line in dmesg.

## What `install` does NOT deploy yet (phase 2b)

This is parity with the `install.sh` this package replaced, not a regression
— but it means the console is **not yet functional from an install alone**:

- **`host/libvirt/hooks/qemu`, the dispatcher.** Without it, libvirt runs no
  hook at all, so the two CPU wrappers that *are* written are never executed.
- `bind-vfio-gpu.sh` / `rebind-host-gpu.sh` — the GPU handover around VM
  start and stop.
- the `started/begin/rules.sh` + `stopped/end/rules.sh` pair — the firewalld
  forward-ports for streaming.
- the hugepage hooks (`00-set-hugepages.sh`, `00-hugepages-fix.sh`,
  `hugepages-reset.sh`).
- the wake-on-demand units `vm-trigger-47984.socket` /
  `vm-trigger-47989.socket` and their services. `vm-wake-gate.py` and
  `handle-vm-start.sh` are placed, but nothing activates them.

All of these ship inside `console/host/`; they are carried onto the target
with the package directory, simply not placed. Wiring them is phase 2b's
work, together with `activate`.

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
moves in phase 2b, after which this package is self-contained and phase 3
is a `git filter-repo --path console` away.
