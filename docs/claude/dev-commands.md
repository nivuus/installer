# Development commands (ISO, Windows guest, host scripts)

## Development Commands

```bash
# ── Installer ISO ──────────────────────────────────────────────────────────
cd installer && sudo make build-iso     # full live-build ISO (needs live-build)
make test-portal                        # wizard on :8080, no install
make test-vm                            # QEMU UEFI boot; portal via Ethernet fallback
                                        # (WiFi AP is not emulable in QEMU)

# install engine, staged — stops before the destructive steps
sudo python3 installer/install-engine/run.py --stop-after partition

# ── Windows guest (console/guest/, moved out of installer/windows-guest/) ──
python3 console/guest/build.py    # unattended LTSC ISO
python3 console/guest/domain.py xml   # inspect the generated domain XML;
# works today (measured 2026-08-28) - see "domain.py is REPAIRED" in docs/claude/console-activate-lessons.md.
python3 console/guest/retro_sync.py   # retrogaming (OPTIONAL): replay
# `retro install` with the owner's manifest, refresh the durable witness on
# D:\state\retro.status, then hold Steam and sync the library. It REFUSES to
# sync unless that witness says `ok` for the current provisioning run: `retro
# scan` builds emulator paths from the manifest without checking they exist, so
# syncing a partial install fills Steam with entries that do not start. On a
# console where retrogaming was never enabled it says so and exits 0. It also
# refuses (exit 7) while a streaming session is live - stopping Steam would cut
# the game being played, and nothing restarts it before the next Moonlight
# connection; --force overrides, loudly.
#
# It ALSO refuses (exit 8) when the console is not running the wheel this host
# built. Why that guard exists (retro's debt D6, fixed 2026-08-29): both wheels
# carried `0.1.0`, so `pip install --no-index --upgrade retro` answered
# "Requirement already satisfied" and installed NOTHING - a fix committed in
# nivuus/retro could stay inert on the console while the error it produced
# still described the original symptom. The wheel now carries a version that
# MOVES (`0.1.0+<timestamp>.<digest>[.g<sha>]`, burned in by an in-tree PEP 517
# backend), `retro identite` prints it, and the durable witness gained a
# `package=` key between `emulation_root=` and `report:`. That key is written by
# BOTH writers - Write-RetroStatus (retro-status.ps1) and format_witness
# (retro_sync.py) - and test_windows_guest_retro_sync.py compares the two key
# lists, so renaming it on one side alone fails a test. The host reference is
# read from the wheel's METADATA (never its filename: pip escapes the `+` to
# `_`) at /var/lib/nivuus/guest/payload/retro/wheels/. No reference wheel is NOT
# a mismatch - it warns and lets through. The remedy is `--reinstaller-le-paquet`
# (copies the wheelhouse to /media/data/Console/retro/wheels, which the guest
# reads as G:\retro\wheels, re-runs pip, re-reads the identity, still exits 8 if
# the gap persists) - a refusal without a remedy would leave a couch console
# unsyncable. NOT measured against the real console yet (task 6 of the plan).

# ── Host scripts ───────────────────────────────────────────────────────────
# The three below also run as `cd installer && make test-scripts`, and as a
# prerequisite of `make test-packages` - they were hand-run only until 2026-08-29.
scripts/tests/test_pcie_wifi_link_guard.sh  # 16 assertions on a fake sysfs tree
scripts/tests/test_hw_blackbox.sh           # 27 assertions on a fake hwmon tree
scripts/tests/test_net_rps_ecores.sh        # 20 assertions on a fake hybrid CPU
console/tests/test_vm_wake_gate.py
console/tests/test_handle_vm_start.sh       # 10 assertions on a fake virsh
console/tests/test_vm_control.sh            # 12 assertions on fake systemctl and logger
console/tests/test_vm_idle_shutdown.sh     # 9 assertions on fake virsh, logger and conntrack
scripts/disk-maintenance.sh --dry-run       # ALWAYS this first when / fills up
```
