#!/usr/bin/env python3
"""Build nivuus-unattend.iso from a Windows 11 IoT Enterprise LTSC medium.

The Windows medium is only read: it is never rebuilt (see unattend_iso.py).
The product key is read from a 0600 file and never passed on the command line,
where it would leak into ps output and shell history.

Usage:
    sudo python3 build.py --windows-iso /media/backup/en-us_windows_11_iot_enterprise_ltsc_2024_x64_dvd_f6b14814.iso \
                          --drivers-dir /media/data/nivuus-win-payload \
                          --output /media/data/iso/nivuus-unattend.iso
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apollo  # noqa: E402
import autounattend  # noqa: E402
import media  # noqa: E402
import payload  # noqa: E402
import unattend_iso  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_KEY_FILE = "/root/.config/nivuus/windows-ltsc.key"
DEFAULT_PASSWORD_FILE = "/root/.config/nivuus/windows-admin.pass"
DEFAULT_APOLLO_PASSWORD_FILE = "/root/.config/nivuus/apollo-ui.pass"


def read_secret(path: str, what: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"missing {what}: {path}")
    mode = p.stat().st_mode & 0o077
    if mode:
        raise SystemExit(f"{path} is group/world readable ({oct(mode)}); chmod 600 it")
    value = p.read_text().strip()
    if not value:
        raise SystemExit(f"{path} is empty")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Build the Nivuus unattend ISO")
    ap.add_argument("--windows-iso", required=True,
                    help="official Windows 11 IoT Enterprise LTSC 2024 medium")
    ap.add_argument("--drivers-dir", required=True,
                    help="directory holding nvidia/, apollo/ and the other "
                         "offline payload binaries (SudoVDA rides inside the "
                         "Apollo installer, no separate directory needed)")
    ap.add_argument("--output", default="/media/data/iso/nivuus-unattend.iso")
    ap.add_argument("--key-file", default=DEFAULT_KEY_FILE)
    ap.add_argument("--password-file", default=DEFAULT_PASSWORD_FILE)
    ap.add_argument("--apollo-password-file", default=DEFAULT_APOLLO_PASSWORD_FILE)
    ap.add_argument("--apollo-user", default="nivuus")
    ap.add_argument("--disk-mode", default="wipe",
                    choices=list(autounattend.DISK_MODES),
                    help="wipe partitions the whole disk; rebuild reformats C: "
                         "and leaves the games partition alone")
    ap.add_argument("--target-disk-verified", action="store_true",
                    help="required with --disk-mode rebuild: confirms the "
                         "target disk was partitioned by this tooling")
    ap.add_argument("--system-partition-gb", type=int, default=200,
                    help="size of C: in GiB; the rest of the disk becomes D:")
    ap.add_argument("--hostname", default="NIVUUS-WIN")
    ap.add_argument("--image-name", default=None,
                    help="pick an image explicitly when the medium has several")
    return ap.parse_args(argv)


def enforce_disk_mode_guard(disk_mode: str, target_disk_verified: bool) -> None:
    """Refuse a rebuild the operator has not explicitly signed off on.

    Nothing downstream can prevent a rebuild from reformatting the wrong
    partition: Windows Setup repartitions in the windowsPE pass, long before
    any guest-side script runs (see the "Real guard is in build.py." comment
    on UnattendParams.disk_mode). The only real gate is the operator saying,
    on this command line, that they checked. Refuse rather than assume.
    """
    if disk_mode == "rebuild" and not target_disk_verified:
        raise SystemExit(
            "--disk-mode rebuild reformats partition 3 of disk 0 in place and "
            "trusts that partition 4 holds your games. Nothing verifies that "
            "for you. Re-run with --target-disk-verified once you have "
            "confirmed the target disk was partitioned by this tooling."
        )


def main(argv=None) -> int:
    args = parse_args(argv)
    enforce_disk_mode_guard(args.disk_mode, args.target_disk_verified)
    key = read_secret(args.key_file, "product key file")
    password = read_secret(args.password_file, "administrator password file")
    apollo_password = read_secret(args.apollo_password_file,
                                  "Apollo web UI password file")

    try:
        print(f"inspecting {args.windows_iso}")
        image = media.inspect_iso(args.windows_iso, image_name=args.image_name)
        print(f"  image #{image['index']}: {image['name']} "
              f"(edition {image.get('edition_id')}, build {image['build']})")

        params = autounattend.UnattendParams(
            product_key=key, admin_password=password,
            image_name=image["name"], hostname=args.hostname,
            disk_mode=args.disk_mode,
            system_partition_mb=args.system_partition_gb * 1024,
        )
        answer_file = autounattend.render(params)

        build_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

        with tempfile.TemporaryDirectory(prefix="nivuus-unattend-") as tmp:
            # Rendered into the same temporary tree as the answer file: the
            # secrets file must never exist on disk outside this build.
            config = Path(tmp) / "config"
            config.mkdir()
            ap_params = apollo.ApolloParams(ui_username=args.apollo_user,
                                            ui_password=apollo_password)
            (config / "sunshine.conf").write_text(apollo.render_conf(ap_params))
            (config / "apps.json").write_text(apollo.render_apps(ap_params))
            (config / "secrets.psd1").write_text(
                apollo.render_secrets(password, args.apollo_user, apollo_password))
            sources = payload.PayloadSources(
                provision_dir=HERE / "provision", probe_dir=HERE / "probe",
                drivers_dir=Path(args.drivers_dir), config_dir=config)

            stage = Path(tmp) / "stage"
            stage.mkdir()
            (stage / "autounattend.xml").write_text(answer_file)
            payload.stage_payload(stage / "nivuus", sources,
                                  payload.marker_text(image["name"], build_id))
            payload.verify_staged(stage / "nivuus")
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            unattend_iso.build_iso(stage, out)

        os.chmod(out, 0o600)
        unattend_iso.verify_iso(out)
        print(f"\nwrote {out} ({out.stat().st_size // 1024} KiB)")
        print(f"  sha256 unattend : {sha256(out)}")
        print(f"  sha256 windows  : {sha256(Path(args.windows_iso))}")
        print("\nAttach BOTH ISOs to the guest: the LTSC medium boots, this one is "
              "only read.")
        print(f"  {out} contains the product key, the administrator password "
              "and the Apollo web password in cleartext (all three are needed "
              "for an unattended offline install) - it is mode 0600, keep it "
              "that way.")
    except (media.MediaError, autounattend.UnattendError,
            payload.PayloadError, unattend_iso.IsoError,
            apollo.ApolloError) as exc:
        raise SystemExit(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
