# console — la console de jeu Windows, en package Nivuus

This directory is a **Nivuus package** (`nivuus.dev/v1`): the installer
engine discovers it, offers it in the wizard, and installs it through the
same three phases any third-party package goes through. It is not special —
that is the point. If the API were not enough for this, it would not be
enough for anyone.

| Phase | What it does |
|---|---|
| `resolve` | Read-only. Derives `vfio-pci.ids` from the discrete GPU's PCI slot and the dedicated NVMe, `nohz_full` from the CPU topology, and the hugepage budget from host RAM. **Refuses**, with a reason, a machine with no discrete GPU or no properly isolated NVMe. |
| `install` | Deploys the libvirt hooks, the host-side scripts and the wake-on-demand units onto the target. |
| `activate` | **Not implemented yet** (phase 2b). The Windows guest is still built by hand with `installer/windows-guest/build.py`. |

## PCI passthrough only

The dedicated disk is handed over as a **whole PCI device**, never as a disk
image or a virtio block device. A machine whose NVMe cannot be detached from
the host is refused in `resolve` rather than silently downgraded — a console
that boots but performs like a laptop is worse than one that says why it
cannot be installed.

## What is not here yet

`installer/windows-guest/` (the unattended LTSC build, the libvirt domain
generator, the provisioning scripts) still lives outside this directory. It
moves in phase 2b, after which this package is self-contained and phase 3
is a `git filter-repo --path console` away.
