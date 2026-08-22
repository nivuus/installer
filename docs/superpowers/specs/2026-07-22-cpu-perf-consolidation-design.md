# CPU performance management consolidation

**Date**: 2026-07-22
**Status**: implemented 2026-07-22 (packages purged, PPD masked, multi-user.target set, watchdog already applied). Reboot-pending: verification steps 1/4 (EPP race gone) and the unit-diff after reboot.

## Context

Five units on this host claim the right to set CPU frequency or power policy.
Only one of them does anything useful.

| Actor | State | What it actually does |
| --- | --- | --- |
| `optimize-cpu-thermal.sh` | active | RAPL PL1/PL2, per-core-type frequency caps, differentiated EPP, nct6798 fan curve |
| `power-profiles-daemon` | active | overwrites EPP on all 24 threads |
| `tuned` | **failed every boot** | nothing |
| `cpufrequtils` | active | nothing — `/etc/default/cpufrequtils` is empty |
| `loadcpufreq` | active | nothing |

**`tuned` has been losing a start race since the trixie upgrade.** Its unit
declares `Conflicts=power-profiles-daemon.service`, and PPD arrived on
2026-07-16 22:42 as a GNOME dependency. The unit — a hand-written override in
`/etc/systemd/system/tuned.service`, dated 2023 — also carries
`ExecStartPre=/bin/sleep 30`, so gdm's greeter D-Bus-activates PPD while tuned
is still sleeping and systemd resolves the conflict by killing tuned:

```
12:37:07  Starting tuned.service            → enters the 30 s sleep
12:37:33  dbus activates org.freedesktop.UPower.PowerProfiles (gnome-shell)
12:37:33  tuned.service: killed, status=15/TERM
12:37:39  Started power-profiles-daemon.service
```

That same override is self-defeating anyway: `PrivateNetwork=yes` makes
`/proc/sys/net` unwritable and `ProtectSystem=full` makes `/etc/tuned`
read-only, so even when tuned did run (2026-07-17 → 2026-07-22) it applied only
part of its profile, logging permission errors for every network sysctl.

**PPD actively breaks the thermal script.** Both units start in the same
second, and PPD wins:

```
cpu-thermal-optimization.service  ExecMainStart = 14:52:13
power-profiles-daemon.service     ExecMainStart = 14:52:13

optimize-cpu-thermal.sh:69 sets E-cores to EPP `power`
observed on all 24 threads:      EPP `balance_performance`
```

Reproduced on a fresh boot on 2026-07-22. The E-core EPP differentiation is
silently lost on every single boot.

**No off-the-shelf daemon can replace the script.** Verified in
`/usr/lib/python3/dist-packages/tuned/plugins/`: `plugin_cpu.py` has zero
occurrences of `scaling_max_freq`, and there is no RAPL or powercap plugin at
all. tuned and PPD can set governor and EPP — precisely the two knobs they
fight the script over — and nothing else. `fancontrol` would be a regression:
the script programs the nct6798's own Smart Fan IV curve (`pwm1_enable = 5`),
so regulation survives userspace dying, which a polling daemon does not.

## Goals

- One owner per knob.
- Remove units that exist to manage CPU policy and manage nothing.
- No functional regression.

## Non-goals

- **Changing the thermal contract.** RAPL stays PL1 = 50 W / 4 s, PL2 = 58 W.
- **Adopting `thermald`.** Justified by the 2026-07-22 campaign but out of
  scope here — see "Follow-up" below.
- The VM-aware gaming/idle switch, specified separately in
  `2026-07-22-vm-aware-cpu-power-mode-design.md`.

## Design

### Target ownership

| Knob | Owner |
| --- | --- |
| RAPL, frequency caps, EPP, fan curve | `optimize-cpu-thermal.sh` |
| gaming/idle switch | `nivuus-cpu-mode@.service` (separate spec) |
| cpuset placement | `vm-cpu-partition.sh` (unchanged) |
| hardware watchdog | systemd (PID 1) — **already applied** |
| GNOME PowerProfiles D-Bus API | none, accepted |

### Already applied (2026-07-22)

`/etc/systemd/system.conf.d/10-nivuus-watchdog.conf` sets
`RuntimeWatchdogSec=30`. The `watchdog` package was running but never opened
`/dev/watchdog` — `watchdog-device` stayed commented, so
`/sys/class/watchdog/watchdog0/state` read `inactive` and the daemon's own
startup log listed every check as disabled. PID 1 now holds the device;
verified re-armed automatically after the 14:51 reboot, `bootstatus = 0`.

### Steps

Back up `/etc/tuned/`, `/etc/systemd/system/tuned.service` and
`/etc/watchdog.conf` to `/media/backup/perf-consolidation-20260722/` first.

1. `apt purge tuned tuned-utils tuned-utils-systemtap`, then remove
   `/etc/systemd/system/tuned.service` and `/etc/tuned/`. Sysfs state is
   already clean — tuned reverted its profile at 2026-07-22 12:07, visible in
   `/var/log/tuned/tuned.log`.
2. `systemctl mask power-profiles-daemon.service`. **`mask`, not `disable`**:
   it is `Type=dbus` *and* `WantedBy=graphical.target`, so disabling leaves
   both activation paths open — that is exactly how it killed tuned.
3. `apt purge cpufrequtils` (takes `loadcpufreq` with it).
4. `apt purge watchdog` (takes `wd_keepalive`; `nfs-common` only *Suggests*
   it, nothing requires it).
5. `systemctl enable wtmpdb-update-boot.service` — the only unit that is
   `WantedBy=graphical.target` alone and still does real work.
   `accounts-daemon` and `switcheroo-control` are D-Bus activatable, and
   `udisks2` is additionally pulled by `haos-agent.service`.
6. `systemctl set-default multi-user.target`. GNOME stays installed and
   startable on demand with `systemctl start gdm`; with PPD masked, even a
   hand-started session cannot resurrect it.

## Verification

1. `cat /sys/devices/system/cpu/cpu{0,16}/cpufreq/energy_performance_preference`
   → `balance_performance` / **`power`**. This is the regression the whole
   change exists to fix; it currently reads `balance_performance` on both.
2. RAPL unchanged: `constraint_0_power_limit_uw` = 50000000,
   `constraint_1_power_limit_uw` = 58000000.
3. `cat /sys/class/watchdog/watchdog0/state` → `active`, `bootstatus` → `0`.
4. **Reboot**, then re-check 1–3. Only a reboot proves the race is gone.
5. `systemctl is-active wtmpdb-update-boot` → `active`; `systemctl --failed`
   empty.
6. `systemctl start gdm` → local session works, `powerprofilesctl` absent.

## Risks

- **A misclassified unit stops starting at boot, with no trace.** This is the
  `docker.service` failure mode of 2026-07-16, where systemd broke an ordering
  cycle by silently dropping a start job. Mitigation: capture
  `systemctl list-units --state=active` before and after the reboot and diff
  it, not just `systemctl --failed`.
- **Purging `tuned` deletes `/etc/tuned/`.** The backup covers rollback.
- GNOME loses its Energy panel profile selector. Accepted.

## Rollback

`systemctl unmask power-profiles-daemon`, `systemctl set-default
graphical.target`, reboot. To drop the watchdog as well, delete
`/etc/systemd/system.conf.d/10-nivuus-watchdog.conf` and `systemctl
daemon-reexec`. Packages are reinstallable; their configs come from the backup.

## Follow-up: thermald is justified, on measured grounds

The 2026-07-22 campaign (`scripts/thermal-campaign/`, ambient 26.3 °C) measured
what a static power cap cannot express. At an identical 50.0 W package budget:

| Load shape | Package temp |
| --- | --- |
| 24 threads saturated (S1) | 69 °C |
| ~2 threads, GPU at 146 W (S3) | 82 °C median, 93 °C peak, at only 41.6 W |
| 24 threads + GPU at 146 W (S4) | 78 °C |

Two conclusions. First, temperature tracks the *shape* of the load, not just
its power: 8 W less concentrated in two boosting cores runs 13 °C hotter than
50 W spread over 24. That reconciles this campaign with the 2026-07-17 figure
of "50 W ≈ 85 °C" — that one was measured under gaming, a light-thread
high-boost load, thermally identical to S3. Both numbers are right.

Second, S1 versus S4 isolates pure ambient sensitivity: same 50.0 W in, case
air +3 K, package **+10 K**. A cap denominated in watts is structurally blind
to this.

So the watt is the wrong control variable. One setpoint cannot serve both
shapes: tuned for the gaming worst case it needlessly throttles all-core work
that has 30 °C of headroom; tuned for all-core it would let gaming approach
TjMax — and S3, which *is* the gaming thermal profile, already peaks at 93 °C,
7 °C from it. A loop targeting temperature grants power when the load shape is
thermally cheap and withdraws it when it is not.

That work needs its own spec and its own calibration campaign, and must
configure thermald to drive `rapl_controller` only — it also writes the global
`max_perf_pct`, which would make it a second writer on frequency.

Campaign caveats worth carrying forward: `scaling_cur_freq` under HWP reports
the requested P-state, not work done, so every MHz column is unusable; Plex ran
throughout, which affects work-done metrics but not temperatures; and
`monitor.sh` aborts on a single sample, which tripped on a transient 96 °C
spike — add debouncing before the next run.
