"""Step 7: install the kernel and GRUB (UEFI) inside the chroot.

Kernel parameters contributed by packages arrive here already resolved, and are
written ONCE while GRUB is being installed. That is the only sane moment: it is
the one point where the file is created rather than edited, so there is nothing
to append to twice. install.sh used to sed them in afterwards and needed a
guard against re-running; the guard is unnecessary when nobody edits.
"""
from __future__ import annotations

import os

from .util import StepError, chroot_run, chroot_stream, write_file


def grub_defaults(extra_cmdline: tuple[str, ...] = ()) -> str:
    """Render /etc/default/grub with the packages' kernel parameters appended.

    Order matters and is stable: `quiet` first, then package parameters in the
    order the packages were resolved, de-duplicated. A parameter containing a
    double quote would break the file, so it is refused rather than escaped -
    no legitimate kernel parameter needs one.
    """
    params = []
    for param in extra_cmdline:
        param = (param or "").strip()
        if not param or param in params:
            continue
        if '"' in param:
            raise ValueError(
                f"kernel parameter {param!r} contains a double quote, which "
                "cannot be written to /etc/default/grub")
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
