"""Step 7: install the kernel and GRUB (UEFI) inside the chroot.

Kernel parameters contributed by packages arrive here already resolved, and are
written ONCE while GRUB is being installed. That is the only sane moment: it is
the one point where the file is created rather than edited, so there is nothing
to append to twice. install.sh used to sed them in afterwards and needed a
guard against re-running; the guard is unnecessary when nobody edits.
"""
from __future__ import annotations

import os
import re

from .util import StepError, chroot_run, chroot_stream, write_file

# /etc/default/grub is SOURCED AS SHELL by grub-mkconfig, so a parameter from a
# third-party package is shell text evaluated as root at install time. A denylist
# cannot be exhaustive against that; only an allowlist can. This set covers every
# kernel parameter this project emits - intel_iommu=on, iommu=pt, nohz_full=0-15,
# vfio-pci.ids=10de:2786,10de:22bc, root=/dev/nvme0n1p2 - and nothing that carries
# shell meaning (quotes, whitespace, newlines, $, backticks, ; & | ...).
KERNEL_PARAM_RE = re.compile(r"^[A-Za-z0-9_.,:=/@+-]+$")


def grub_defaults(extra_cmdline: tuple[str, ...] = ()) -> str:
    """Render /etc/default/grub with the packages' kernel parameters appended.

    Order matters and is stable: `quiet` first, then package parameters in the
    order the packages were resolved, de-duplicated. Each parameter is checked
    against KERNEL_PARAM_RE and refused (ValueError, naming the parameter and
    the allowed character set) rather than escaped - see the note above.
    """
    params = []
    for param in extra_cmdline:
        if not isinstance(param, str):
            raise ValueError(
                f"kernel parameter {param!r} must be a string")
        param = param.strip()
        if not param or param in params:
            continue
        if not KERNEL_PARAM_RE.match(param):
            raise ValueError(
                f"kernel parameter {param!r} contains characters outside "
                f"{KERNEL_PARAM_RE.pattern} - /etc/default/grub is sourced as "
                "shell by grub-mkconfig, so only letters, digits and "
                "_.,:=/@+- are allowed")
        params.append(param)

    cmdline = " ".join(["quiet", *params])
    return (
        "GRUB_DEFAULT=0\nGRUB_TIMEOUT=3\n"
        'GRUB_DISTRIBUTOR="Nivuus"\n'
        f'GRUB_CMDLINE_LINUX_DEFAULT="{cmdline}"\n'
        'GRUB_CMDLINE_LINUX=""\n'
    )


def install_bootloader(config: dict, target: str, fs: dict, emit,
                       extra_cmdline: tuple[str, ...] = ()) -> None:
    emit.info("bootloader", 70, "Installing kernel and GRUB (UEFI)…")

    chroot_run(target, ["apt-get", "update"])

    packages = ["linux-image-amd64", "grub-efi-amd64", "efibootmgr",
                "firmware-linux-free"]
    code = chroot_stream(
        target,
        ["apt-get", "install", "-y", "--no-install-recommends", *packages],
        on_line=lambda l: emit.info("bootloader", 74, l[:120]),
    )
    if code != 0:
        raise StepError("failed to install kernel/grub packages")

    # Non-free firmware so real NICs/WiFi work on first boot (generic installer).
    # Non-fatal: names vary across releases and not every box needs them.
    emit.info("bootloader", 76, "Installing device firmware (best-effort)…")
    chroot_run(
        target,
        ["apt-get", "install", "-y", "--no-install-recommends",
         "firmware-linux", "firmware-realtek", "firmware-iwlwifi",
         "firmware-misc-nonfree"],
        check=False,
    )

    if extra_cmdline:
        emit.info("bootloader", 77,
                  "Kernel parameters from packages: " + " ".join(extra_cmdline))
    write_file(os.path.join(target, "etc/default/grub"),
               grub_defaults(extra_cmdline))

    emit.info("bootloader", 78, "Writing GRUB to the EFI partition…")
    code = chroot_run(
        target,
        ["grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi",
         "--bootloader-id=Nivuus", "--recheck"],
        check=False,
    )
    if code.returncode != 0:
        # Fall back to removable-media path for firmware that ignores NVRAM.
        emit.warn("bootloader", 78, "grub-install NVRAM failed; using removable path.")
        chroot_run(
            target,
            ["grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi",
             "--bootloader-id=Nivuus", "--removable", "--recheck"],
        )
    chroot_run(target, ["update-grub"])
