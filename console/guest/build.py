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
import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# installer/ root, for `common` - the single source of the retro marker
# path shared with install-engine/steps/features.py (see common/retro.py).
sys.path.insert(0, os.path.dirname(_HERE))

import apollo  # noqa: E402
import autounattend  # noqa: E402
import media  # noqa: E402
import payload  # noqa: E402
import unattend_iso  # noqa: E402
from common.retro import retro_state_path  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_KEY_FILE = "/root/.config/nivuus/windows-ltsc.key"
DEFAULT_PASSWORD_FILE = "/root/.config/nivuus/windows-admin.pass"
DEFAULT_APOLLO_PASSWORD_FILE = "/root/.config/nivuus/apollo-ui.pass"
# Same path install-engine/steps/features.py writes to, resolved against
# the live host's "/" (that install target has become "/" by the time
# this runs). Single source: common/retro.py, imported above.
DEFAULT_RETRO_MARKER = retro_state_path()


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
    # A username, not a secret - deliberately fine on argv/in --help. Only
    # the Apollo web-manager PASSWORD comes from a mode-0600 file, below.
    ap.add_argument("--apollo-user", default="nivuus")
    ap.add_argument("--disk-mode", default="wipe",
                    choices=list(autounattend.DISK_MODES),
                    help="wipe partitions the whole disk; rebuild reformats C: "
                         "and leaves the games partition alone")
    ap.add_argument("--target-disk-verified", action="store_true",
                    help="required with --disk-mode rebuild: confirms the "
                         "target disk was partitioned by this tooling")
    ap.add_argument("--data-partition-gb", type=int, default=820,
                    help="size of the games partition (D:) in GiB; Windows takes "
                         "the rest. Default targets the 1 TB production NVMe "
                         "(Windows gets ~110 GiB); a smaller disk needs an "
                         "explicit value. D: comes FIRST on the disk so "
                         "Windows Setup cannot displace it - see "
                         "templates/autounattend.xml.j2")
    ap.add_argument("--hostname", default="NIVUUS-WIN")
    ap.add_argument("--image-name", default=None,
                    help="pick an image explicitly when the medium has several")
    # Retrogaming (RetroArch, via the `retro` package) is OPTIONAL. Default
    # is None, not False: omitting the flag must mean "do what the wizard's
    # assistant recorded on this host", never "off" - that would silently
    # defeat a box the owner DID check. --retro/--no-retro still lets an
    # operator force either direction explicitly.
    ap.add_argument(
        "--retro", action=argparse.BooleanOptionalAction, default=None,
        help="force retrogaming (RetroArch via the `retro` package) on or "
             "off; without this flag, defaults to whatever the install "
             f"wizard recorded on this host ({DEFAULT_RETRO_MARKER}), or "
             "off if that file is absent")
    return ap.parse_args(argv)


def read_retro_marker(path: str = DEFAULT_RETRO_MARKER) -> bool:
    """Return what the install wizard recorded for retro on this host.

    Written by install-engine/steps/features.py only when "retro" was
    checked - absent otherwise. Only an explicit JSON `true` for "enabled"
    counts as "on"; every other shape resolves to "off", never by
    accident: a missing file, an unreadable one, a document that parses
    but is not a JSON object (`null`, a list, a bare string - all valid
    JSON, none of them something .get() can be called on), a missing
    "enabled" key, and a truthy-but-not-boolean value for it (the string
    "false", the integer 1) all mean the same thing here: no evidence the
    owner explicitly asked for retro. `is True`, not `bool(...)`, is what
    makes that last case refuse a coercion instead of accepting it.
    """
    marker = Path(path)
    if not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return data.get("enabled") is True


def resolve_retro(cli_value: bool | None,
                  marker_path: str = DEFAULT_RETRO_MARKER) -> bool:
    """Decide whether this build enables retro.

    --retro/--no-retro on the command line always wins, in either
    direction: an operator must be able to force the build regardless of
    what the wizard recorded. Only when neither was given (cli_value is
    None) does the assistant's marker decide.
    """
    if cli_value is not None:
        return cli_value
    return read_retro_marker(marker_path)


def build_retro_psd1(cli_value: bool | None,
                     marker_path: str = DEFAULT_RETRO_MARKER) -> tuple[str, bool]:
    """Render config/retro.psd1's content exactly as main() writes it.

    Kept as its own function, separate from main(), so a test can pin what
    actually ends up in the payload for every combination of --retro and
    the host marker, without needing a real Windows medium to run main().
    """
    enabled = resolve_retro(cli_value, marker_path)
    return apollo.render_retro(enabled), enabled


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
    # The umask is the real guarantee: it makes every file this process
    # creates (in particular the multi-hundred-MB ISO xorriso writes, which
    # otherwise inherits the ambient umask - typically world-readable) come
    # into existence already mode 0600, for the entire time it takes to
    # write it, not just after the fact. The os.chmod(out, ...) below is
    # kept too, as a belt-and-braces assertion of the same invariant, not
    # as the mechanism that provides it.
    os.umask(0o077)
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
            data_partition_mb=args.data_partition_gb * 1024,
        )
        answer_file = autounattend.render(params)

        build_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

        with tempfile.TemporaryDirectory(prefix="nivuus-unattend-") as tmp:
            # Rendered into the same temporary tree as the answer file: the
            # secrets file must never exist on disk outside this build.
            config = Path(tmp) / "config"
            config.mkdir()
            (config / "sunshine.conf").write_text(apollo.render_conf())
            (config / "apps.json").write_text(apollo.render_apps(apollo.ApolloParams()))
            (config / "secrets.psd1").write_text(
                apollo.render_secrets(password, args.apollo_user, apollo_password))
            # Always rendered, checked or not: an absent file cannot be told
            # apart from a payload built by a version that predates the
            # option, an explicit Enabled = $false can. --retro/--no-retro
            # wins when given; otherwise this falls back to what the wizard
            # recorded on this host (see build_retro_psd1/resolve_retro).
            retro_psd1, retro_enabled = build_retro_psd1(args.retro)
            (config / "retro.psd1").write_text(retro_psd1)
            if args.retro is None:
                print(f"  retro: {'enabled' if retro_enabled else 'disabled'} "
                      f"(from {DEFAULT_RETRO_MARKER}, no --retro/--no-retro given)")
            else:
                print(f"  retro: {'enabled' if retro_enabled else 'disabled'} "
                      "(--retro/--no-retro given explicitly)")
            sources = payload.PayloadSources(
                provision_dir=HERE / "provision", probe_dir=HERE / "probe",
                drivers_dir=Path(args.drivers_dir), config_dir=config,
                assets_dir=HERE / "assets")

            stage = Path(tmp) / "stage"
            stage.mkdir()
            (stage / "autounattend.xml").write_text(answer_file)
            payload.stage_payload(stage / "nivuus", sources,
                                  payload.marker_text(image["name"], build_id))
            payload.verify_staged(stage / "nivuus")
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            unattend_iso.build_iso(stage, out)

        # Belt and braces: the umask above is what actually keeps the ISO
        # private while it is being written; this chmod only re-asserts the
        # same mode afterwards, in case something in the write path (e.g. an
        # external tool restoring its own default mode) ever weakens it.
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
