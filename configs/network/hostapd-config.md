# hostapd (Wi-Fi access points)

Two QCA9984 4x4 cards (`ath10k_pci`, firmware 10.4-3.9.0.2-00157), one hostapd process,
two networks on each radio. Rewritten after the Wi-Fi audit of 2026-11-09; the previous
version of this file described a 2025 state (ACS, HT40 on 2.4 GHz, 30 dBm) that no longer held.

| Interface | Band | SSID | Bridge | Notes |
|-----------|------|------|--------|-------|
| `wlp10s0` | 5 GHz, ch 36, VHT80 (centre 42) | `mallanic` | `localBridge` | primary BSS of `phy1` |
| `wlp10s1` | 5 GHz | `Wifi Mike` | `publicBridge` | guests, `ap_isolate=1` |
| `wlp11s0` | 2.4 GHz, ch 6, HT20 | `mallanic` | `localBridge` | primary BSS of `phy2` |
| `wlp11s1` | 2.4 GHz | `Wifi Mike` | `publicBridge` | guests, `ap_isolate=1` |

Files: `/etc/hostapd/5Ghz.conf`, `/etc/hostapd/2.4Ghz.conf` (mode 600, they hold the
passphrases). Nothing else in `/etc/hostapd/` is used but `deny_5ghz.txt` (MAC deny list of
the 5 GHz home BSS) and the packaged `ifupdown.sh`. Files retired on 2026-11-09 (`*.new`,
`wlp3.conf`, `6Ghz.conf`, `speed2mesh.conf`, the empty `hostapd.conf`/`hostapd.config`/
`/etc/hostapd.psk`) are archived in `/media/backup/wifi-fix-20261109/`, with the pre-change
configuration (`before-fix-orig.tar.gz`).

## Units

- `configs/systemd/hostapd.service` → `/etc/systemd/system/hostapd.service`. Replaces the
  packaged unit, which can only start `/etc/hostapd/hostapd.conf`: the host used to pass
  `DAEMON_CONF="-t"` and keep an empty `hostapd.conf` for its `ConditionFileNotEmpty`. Names
  both files, **5 GHz first** (hostapd brings radios up in order, and the radio up first
  collects the dual-band clients reconnecting after a restart: 4 of them landed on 2.4 GHz
  when 2.4 GHz came first). `ExecReload` sends SIGHUP: the packaged unit has none, so
  `systemctl reload hostapd` failed (`CanReload=no`) and every change from Home Assistant
  stayed unapplied. **SIGHUP is not hitless**: all 31 clients re-associated, all back within
  25 s (measured). Channel, width, adding or removing a BSS need a restart (~15 s).
  `TimeoutStartSec=60`: the 5 GHz radio runs a 20/40 MHz coexistence scan of ~6 s.
- `hostapd.service.d/50-hardening.conf` (unchanged; see `docs/claude/host-security-hardening.md`).
- `configs/systemd/nivuus-wifi-post.service` + `scripts/nivuus_wifi_post.py` →
  `/usr/local/sbin/nivuus-wifi-post`. Replaces the `guest-isolation.conf`,
  `txpower-24ghz.conf` and `override.conf` drop-ins, whose `ExecStartPost=-… sleep 5/6`
  raced hostapd and ignored their own failures. Started by hostapd's `Wants=`, bound to it;
  waits until hostapd reports each **radio** ENABLED (secondary BSSes report no state of
  their own), then, reading the hostapd files to know which port is which:
  - marks every port on `publicBridge` `isolated` (guests of one radio cannot reach guests
    of the other; `ap_isolate` only covers one radio);
  - holds the 2.4 GHz radio at 20 dBm (the card EEPROM says US = 30 dBm; ETSI and the
    Zigbee/Thread stick in the chassis want 20);
  - publishes 802.11k neighbor reports between the BSSes sharing an SSID.
  Every setting is read back; any failure fails the unit (`systemctl --failed`) without
  touching hostapd, and the other steps still run. Tests: `scripts/tests/test_nivuus_wifi_post.py`.

## Settings and why

Radio, 2.4 GHz: channel 6 HT20 (RF plan in `docs/claude/host-network-rf-wan.md`: Thread
ch 21, Zigbee ch 25); `supported_rates`/`basic_rates` OFDM only (6 Mbit/s floor): beacons and
multicast used to leave at 1 Mbit/s and the home BSS dropped 7.4 % of its multicast
(19 037 of 256 934, mDNS/SSDP discovery). All 2.4 GHz clients were HT-capable when checked.
`rts_threshold=2347` (RTS off; was 1000).

Radio, 5 GHz: `vht_capab` with `[BF-ANTENNA-4][SOUNDING-DIMENSION-4]` (the card is 4x4:
`iw phy phy1 info` VHT caps `0x339b79fa`); `track_sta_max_num=100` for band steering.

Capability strings: hostapd **silently ignores** unknown tokens. Fixed on 2026-11-09:
`[SHORT-GI40]`→`[SHORT-GI-40]`, `[MAXAMSDU-7935]`→`[MAX-AMSDU-7935]`,
`[TX-STBC2BY1]`→`[TX-STBC-2BY1]`, `[TX-ANTENNAPATTERN]`→`[TX-ANTENNA-PATTERN]`, dropped
`[BF-ANTENNA2]`, `[SOUNDING-DIMENSION2]`, `[DSSS_CCK-40]`. Check what the radio really
advertises with `hostapd_cli -i wlp10s0 status` (`vht_caps_info`, `ht_caps_info`).

Every BSS: WPA2/WPA3 transition (`wpa_key_mgmt=WPA-PSK SAE`, the 5 GHz home BSS also
`WPA-PSK-SHA256`), `ieee80211w=1`, `sae_require_mfp=1`, `sae_pwe=2`, `auth_algs=1`;
`rrm_neighbor_report=1`, `rrm_beacon_report=1`; guest BSSes carry their own
`ctrl_interface` (not inherited by a `bss=` section: without it no control socket exists).
2.4 GHz home BSS: `multicast_to_unicast=1`. 2.4 GHz BSSes: `no_probe_resp_if_seen_on=` the
5 GHz BSS of the same SSID (hostapd's built-in band steering: no probe answer to a station
the 5 GHz radio heard within 180 s; 2.4-only stations still see the beacons).

## Checking it

`hostapd_cli` does not answer from a `su`/Claude session (its reply socket lives in the
session's `/tmp`): run it through `systemd-run --pipe --wait hostapd_cli -i <if> <cmd>`.

- clients: `iw dev <if> station dump`; per-client retry ratio = `tx retries / tx packets`.
- multicast losses: the `drops` column of `iw dev wlp11s0 info` (multicast TXQ).
- what is broadcast: `ip link set wlo1 up; iw dev wlo1 scan; ip link set wlo1 down` (the
  AX201 is otherwise unused) shows rates, AKM suites, `MFP-capable`, `RM enabled`.
- post-start settings: `journalctl -u nivuus-wifi-post`, `cat /sys/class/net/wlp1?s1/brport/isolated`.

## Applying a change without losing the session

An SSH session over this Wi-Fi drops during a restart. Apply from a transient unit with a
dead-man rollback: `systemd-run --unit=wifi-apply bash apply.sh` that backs up, installs,
restarts, then restores everything unless a confirmation file appears within N minutes.
That is how the 2026-11-09 changes went in (three applies, one caught failure, no outage
longer than a restart).
