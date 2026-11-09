# WiFi performance: measurements, ruled-out causes, hardware plan

Read before trying to make the WiFi faster, and before changing the WiFi cards or their antennas.

## Baseline (2026-11-09)

The client was a laptop 2 m from the antennas, with an Intel 2x2 card, NetworkManager and `wpa_supplicant`. The server ran `iperf3 -s`. Download is AP→client (`-R`).

| Path | Download | Upload |
|---|---|---|
| WAN, Ookla run on the host itself (Orange, Lyon) | 929 Mbit/s | 719 Mbit/s |
| 5 GHz ch36 VHT80, client MCS9 2SS (866 Mbit/s PHY) | 283-297 Mbit/s (4 streams), 328 (16) | 602-615 Mbit/s |
| 2.4 GHz ch6 HT20, client MCS15 (144 Mbit/s PHY) | 42 Mbit/s | 88 Mbit/s |
| Internet from the laptop over 5 GHz (4 parallel OVH downloads) | 287-320 Mbit/s | - |

Notes on the baseline:

- **The WiFi is the bottleneck, not the WAN.** The `cake` egress shaper (780 Mbit/s) holds upload latency at 7 ms under load. Download under full load raises latency from 11 to 24 ms (28 ms worst case). Ingress shaping was judged not worth its 5-8 % throughput cost.
- **Download is about half of upload on both bands, at the same PHY rate.** This holds for TCP with 1, 4 and 16 streams and for UDP (191 Mbit/s with 21 % loss).

## Causes ruled out for the download ceiling

- **Host CPU:** an i9-12900K, with `iperf3` at 1 %.
- **Channel occupancy:** 17 % busy on 5 GHz.
- **Qdiscs:** the WiFi and bridge interfaces are `noqueue`, which is normal with mac80211 TXQs.
- **Retries:** 7.5 % on 5 GHz at MCS9.
- **TCP Small Queues on a local sender:** internet traffic forwarded through the router shows the same ceiling.
- **Modified firmware:** `firmware-5.bin` is stock `10.4-3.9.0.2-00157`, not Candela CT. The `.backup` copy is byte-identical.

### AQL tuning

AQL cannot be tuned at runtime. The Debian kernel is built without `CONFIG_MAC80211_DEBUGFS` and `CONFIG_ATH10K_DEBUGFS`, so `/sys/kernel/debug/ieee80211/phy*/` is empty.

### `frame_mode=2` (802.3 encapsulation offload), tried and reverted

The upstream patch claims +8 % download (365→396 Mbit/s on an R7800 with QCA9984). On this host, in the same session, with the same client:

| | 4 streams | 16 streams | Upload |
|---|---|---|---|
| `frame_mode=1` (before) | 283-297 Mbit/s | 328 Mbit/s | 602 Mbit/s |
| `frame_mode=2` | 257 Mbit/s | 299 Mbit/s | 595 Mbit/s |

There was no gain, so the change was reverted. The scripts that ran the reload, with a dead-man rollback, are in `/root/work/wifi-perf-20261109/`. A module reload (`modprobe -r ath10k_pci ath10k_core`) cut the WiFi for about 3 s and left no AER trace.

**Conclusion:** the ceiling of about 300 Mbit/s is the ath10k / QCA9984 downlink path itself. Upstream reports put this chip at about 400 Mbit/s for a 2SS client. No software setting left on this kernel moves it.

## 5 GHz receive loss (hardware)

At 2 m, the free-space path loss is about 53 dB at 5.2 GHz and 46 dB at 2.4 GHz.

- The 5 GHz card (`0000:0a:00.0`) hears the client at about **-69 dBm on all four chains** (`[-69, -68, -70, -70]`). The expected level is -35 to -40 dBm.
- The client hears the card at -44 to -50 dBm, against an expected level of about -30 dBm.
- The 2.4 GHz card loses only about 8 dB.

The connectors are tight, and the antennas are **already remote-mounted** (not on the case bracket), so placement is not the cause. A uniform loss of 20-30 dB, on 5 GHz only, on every chain, points at the antennas or their cables not covering 5 GHz. The loss does not cap a close client, which still gets MCS9. It does cost most of the 5 GHz range, which is why distant dual-band clients sit on 2.4 GHz.

## Planned replacement: 2 x AsiaRF AW7916-AED (MediaTek MT7916AN)

The user owns two modules.

### The module

| Item | Value |
|---|---|
| Form factor | M.2 A+E key, size **3052** (30 x 52 mm) |
| Radios | G-band 2T2R (2.4 GHz, 40 MHz max) + A-band 2T3R 2ss (**5 *or* 6 GHz**, 160 MHz max), dual-band concurrent |
| Antenna connectors | 3 IPEX, shared by both bands. MHF1 vs MHF4 is not confirmed: check the module before buying cables. |
| Power | 3.3 V, **3 A recommended (2.5 A minimum)**, 9-10 W max per module |
| Driver | `mt7915e` with `mediatek/mt7916_{rom_patch,wm,wa}.bin` and `mt7916_eeprom.bin`. All are already present on the host (Debian 13, kernel 6.12). |

### Mounting

The modules **must not go on the current carrier.** That carrier is a PCIe x1 card with an ASMedia ASM1182e switch feeding both QCA9984 cards.

- **Power:** two modules need up to 6 A on 3.3 V, while a PCIe x1 slot supplies 3 A on 3.3 V for the whole card.
- **Size:** common mini-PCIe to M.2 adapters (Delock 63909, Ableconn MPEX-M2WL) do not take 3052.

Use one **AsiaRF ACPCX1-201** (PCIe x1 to M.2 A+E, with an on-board 12 V to 3.3 V converter) per module, each in its own slot.

One module replaces the whole QCA9984 carrier, because it runs 2.4 GHz and 5 GHz concurrently. The second module is for 6 GHz. It needs a second free PCIe slot on the ROG STRIX B660-G, which is still to be checked.

6 GHz means SAE only, with H2E (`sae_pwe=1`), and most IoT clients cannot join it. It is an extra band, not a replacement.

### Antennas and cables

Per module: **3 tri-band antennas** (2.4 / 5.15-7.125 GHz, RP-SMA) and **3 IPEX to RP-SMA bulkhead pigtails**, in 1.13 mm coax, kept as short as possible. AsiaRF sells a bundle with tri-band antennas and pigtails.

### Software work when the hardware is in

All of these name the QCA9984 interfaces (`wlp10s*`, `wlp11s*`) or ath10k-specific capabilities:

- `/etc/hostapd/{2.4Ghz,5Ghz}.conf`: HE capabilities, `ieee80211ax=1`. The VHT capability tokens are ath10k-specific.
- `hostapd.service` and `nivuus-wifi-post` (interface names, txpower).
- The PCIe link guard and the black box (`wifi_<pci>_c` reads ath10k hwmon).
- The MQTT agent's hostapd feature.

Re-run the measurements above against the same client to compare.
