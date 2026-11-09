# Host security hardening (2026-11-08 / 09 audit)

Files in `configs/systemd/` deployed by hand to `/etc/systemd/` (no installer step deploys
them yet). After copying: `systemctl daemon-reload`, then restart the unit.

## Service sandboxing: SHH where it works, by hand where it breaks

[SHH](https://github.com/desbma/shh) profiles a unit with strace and writes its options. It
hardened most services on 2026-11-08 but **cannot** be used on these, and each one broke when
tried:

| Unit | Why SHH fails | Drop-in |
|---|---|---|
| `clamav-daemon` | socket-activated: the `shh run` wrapper receives `LISTEN_FDS`, clamd then binds its own socket and dies ("Socket file ... in use") | `clamav-daemon.service.d/50-hardening.conf` |
| `clamav-clamonacc` | fanotify breaks under strace | `clamav-clamonacc.service.d/50-hardening.conf` |
| `auditd` | `Type=forking`; the generated options made it fail "Cannot daemonize" | `auditd.service.d/50-hardening.conf` (minimal set only) |
| `atop` | `KillSignal=SIGUSR2` kills the `shh run` wrapper, so no profile is written | `atop.service.d/50-hardening.conf` |
| `hostapd` | not profiled (Wi-Fi outage per attempt) | `hostapd.service.d/50-hardening.conf` |
| `smbd` | not profiled (would need every client operation during the window) | `smbd.service.d/50-hardening.conf` (replaces an older `harden.conf`) |

Exposure (`systemd-analyze security`) before → after: clamd 9.6 → 5.6, clamonacc 9.6 → 5.4,
atop 9.6 → 5.3, hostapd 9.6 → 6.3, auditd 8.9 → 7.0, smbd 9.0 → 4.9.

**Options left out on purpose (each one breaks something):**

- **hostapd, no `PrivateTmp`.** hostapd answers a control client (`hostapd_cli`, the mqtt
  agent's `hostapd_manager`) on the socket that client bound in *its own* `/tmp`. With a
  private `/tmp`, every reply goes to the wrong place and every query times out. A Claude
  session has this exact problem in the other direction: reach `/run/hostapd/<iface>` from a
  socket bound under `/run`.
- **hostapd, no `ProtectKernelTunables` or `PrivateDevices`.** It and its `ExecStartPost`
  lines write `/proc/sys` and `/sys` (bridge port flags, txpower), and it watches `/dev/rfkill`.
- **ClamAV, no `ProtectHome` or `PrivateTmp`.** clamd scans any path anywhere.
- **smbd, no `ProtectHome` and only `ProtectSystem=yes`.** The `[projects]` share is under
  `/home`; `ExecStartPre` rewrites the AppArmor share list in `/etc`, and `unix password
  sync` runs `passwd`. Capabilities are a deny-list, not an allow-list: it needs SETUID/SETGID,
  LEASE (kernel oplocks), DAC override and chown. `RestrictSUIDSGID` is safe only while no
  share directory carries the setgid bit (none did on 2026-11-09).
- **No capability limits on any of them.** atop needs root's view of `/proc` and taskstats,
  and hostapd needs `CAP_NET_ADMIN` and `CAP_NET_RAW`.

Restarting hostapd drops Wi-Fi for about 10 s. Clients come back on 2.4 GHz first, because
5 GHz runs an HT scan before it is ENABLED, so the 5 GHz client count stays low for a while.
That is band choice, not breakage: compare the *total* station count.

`wpa_supplicant` is **masked**: NetworkManager manages no Wi-Fi device here, since every
radio is hostapd's, and the daemon had 0 interfaces. Unmask it only if the host ever needs to
be a Wi-Fi client.

## ClamAV alerts do not use `VirusEvent`

clamd runs `VirusEvent` as user `clamav` through `/bin/sh`, and the `usr.sbin.clamd` AppArmor
profile denies that exec (proven with strace: `execve("/bin/sh") = -EACCES`). ClamAV ≥ 1.0
also replaces `%f` with a fixed warning sentence. The `VirusEvent` line is commented out in
`/etc/clamav/clamd.conf`. Alerts come from the mqtt agent instead, which follows clamd in the
journal, through HA automation `securite_clamav_virus_detecte`. See the ClamAV section of
the `mqtt` CLAUDE.md.

## Journal sealing (Forward Secure Sealing)

`journald.conf.d/60-nivuus-seal.conf` sets `Seal=yes`. The keys were generated once on
2026-11-09 with `journalctl --setup-keys --interval=15min`:

- The sealing key is `/var/log/journal/<machine-id>/fss`. It evolves forward, so root
  obtained later cannot re-seal earlier entries.
- The **verification key is kept off the host by the owner**, in a password manager. Do not
  store it on the host: anyone holding it can forge seals.
- Check with `journalctl --verify --verify-key=<key>`. Without the key the result is `FAIL
  (Required key not available)`, which is expected.
- Only journal files created after `Seal=yes` are sealed. Run `journalctl --rotate` once
  after enabling it.
- Regenerating the keys with `--setup-keys --force` invalidates the old verification key for
  new files.

## lynis

- lynis 3.1.7 comes from the CISOfy repo. `lynis.service` and `lynis.timer` come from
  upstream `extras/systemd`: `Type=oneshot`, no `[Install]`, timer only.
- **`lynis.service.d/10-c-locale.conf` (`LC_ALL=C`)**: lynis parses command output in
  English. In the French locale, `apt-cache policy` prints `Installé`, and KRNL-5788 always
  failed. Upstream knows about it (CISOfy/lynis#1195, closed without a fix). Run it by hand
  as `LC_ALL=C lynis audit system` too.
- Skips live in `/etc/lynis/custom.prf`, each commented with its reason. Prefer an atomic
  skip such as `skip-test=KRNL-6000:kernel.modules_disabled` over skipping a whole test.
  Modules must stay loadable for VFIO hooks, firewalld helpers, the 4G `cdc_ncm` fallback,
  PPPoE and usbip.
- Score 89, 0 warnings on 2026-11-09. The three suggestions left are by choice:
  - **SSH-7408**: the port stays 22. WAN SSH is the owner's way in, using keys only, with
    `AllowUsers` and CrowdSec.
  - **BOOT-5264**: lynis always prints it.
  - **LOGG-2154**: now skipped. Tamper-evidence comes from FSS, and the owner wants no
    third-party log host.
