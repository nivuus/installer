#!/usr/bin/env python3
"""Tests for installer/webapp/installer_runner.py: the wizard's secrets.

The config the portal hands the engine carries the account, PPPoE and Wi-Fi
passwords in clear. It used to be written to /run/nivuus-install/config.json,
mode 0644, and never removed. It must reach the engine without ever being a
file. The engine here is a stand-in that reads --config exactly the way
run.py's load_config() does (open the path, json.load it).

Run: python3 scripts/tests/test_webapp_runner.py
"""
import json
import os
import pathlib
import sys
import tempfile
import textwrap

REPO = pathlib.Path(__file__).resolve().parents[2]
failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


with tempfile.TemporaryDirectory() as tmp:
    progress = pathlib.Path(tmp, "run")
    progress.mkdir()
    os.environ["NIVUUS_PROGRESS_DIR"] = str(progress)
    sys.path.insert(0, str(REPO / "installer"))
    sys.path.insert(0, str(REPO / "installer" / "webapp"))
    import installer_runner  # noqa: E402

    received = pathlib.Path(tmp, "received.json")
    engine = pathlib.Path(tmp, "fake_run.py")
    engine.write_text(textwrap.dedent(f"""\
        import argparse, json
        parser = argparse.ArgumentParser()
        parser.add_argument("--config")
        parser.add_argument("--target")
        parser.add_argument("--nivuus-src")
        args = parser.parse_args()
        with open(args.config) as fh:          # run.py's load_config()
            config = json.load(fh)
        with open({str(received)!r}, "w") as fh:
            json.dump(config, fh)
        """))
    installer_runner.ENGINE_RUN = str(engine)

    # A config.json left behind by an older portal.
    stale = pathlib.Path(installer_runner.CONFIG_FILE)
    stale.write_text('{"user": {"password": "old-secret"}}')

    config = {"user": {"username": "alice", "password": "s3cret-é"},
              "wan": {"pppoe_password": "ppp-secret"},
              "wifi_ap": {"private_passphrase": "wifi-secret-123"}}
    runner = installer_runner.InstallRunner()
    runner.start(config, nivuus_src=tmp, target=tmp)
    code = runner._proc.wait(timeout=30)

    check("the engine exits cleanly", code, 0)
    check("the engine receives the whole config, non-ASCII included",
          json.loads(received.read_text()) if received.exists() else None, config)
    check("no config file is written for the engine", stale.exists(), False)
    leftovers = [p.name for p in progress.iterdir()
                 if "secret" in p.read_text(errors="replace")]
    check("nothing under the progress directory holds a secret", leftovers, [])

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - installer runner tests passed")
