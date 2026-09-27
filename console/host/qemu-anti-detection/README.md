# Anti-detection QEMU

The console's domain does not run Debian's `qemu-system-x86_64`: it runs
QEMU 10.2.2 built by `build-qemu.sh` with `qemu-10.2.2.patch` applied,
installed under `/opt/qemu-anti-detection` (prefix and stamp contract in
`console/qemu_build.py`, which also wires the build as the `qemu` step of
`guest_steps.plan_steps`, right before `define`).

## Provenance of the patch

`qemu-10.2.2.patch` is vendored from
<https://github.com/zhaodice/qemu-anti-detection>, commit
`2750c86d2d045243ba6617951487e41b25c05557` (2026-04-18), with **one
deliberate deviation**:

* **The `include/hw/pci/pci.h` hunk that renames the virtio PCI vendor is
  removed** (`PCI_VENDOR_ID_REDHAT_QUMRANET`, `PCI_SUBVENDOR_ID_REDHAT_QUMRANET`
  and `PCI_SUBDEVICE_ID_QEMU`, `1af4`/`1100` -> `8086`). Measured
  2026-09-15 on the production console: with it, the guest boots and
  answers ACPI, but the virtio-win drivers match on
  `PCI\VEN_1AF4&DEV_1041&SUBSYS_11001AF4` and never bind, so the virtio
  NIC sends nothing (tap `rx=0`) and the four virtiofs shares are gone with
  it. The upstream author's own example config avoids virtio entirely for
  that reason; this domain depends on it (bridge NIC, shares). The second
  hunk of that file (`PCI_VENDOR_ID_REDHAT 1b36` -> `8086`, the PCIe root
  ports and xhci controller) is kept: Windows drives those with class
  drivers, not vendor-matched ones.

Everything else is upstream as is: SMBIOS/ACPI strings, device model
names and serials, the KVM CPUID signature handling, the firmware VM bit.

`test_console_host_files.py` asserts the vendored patch never touches
`0x1af4` again, so a re-vendoring that forgets this note fails a test
rather than the console's network.

## Re-vendoring

1. Fetch the upstream patch for the pinned `QEMU_VERSION` in `build-qemu.sh`
   (bump `QEMU_SHA256` alongside if the version moves - it is the tarball's
   checksum, pinned on purpose).
2. Drop the virtio vendor hunk as above.
3. Run `console/tests/test_console_host_files.py` and
   `test_console_guest_steps.py`. The stamp carries the patch's sha256, so
   the next activation rebuilds by itself.

## What it does NOT hide

Timing attacks (RDTSC) and the WMI classes that return no instances in a
VM (Win32_Fan, Win32_VoltageProbe, thermal zones...) are outside this
patch's reach - see the upstream README.
