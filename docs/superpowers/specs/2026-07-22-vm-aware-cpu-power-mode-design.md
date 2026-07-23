# VM-aware CPU power mode

**Date**: 2026-07-22 (extended the same day with the C-state dimension)
**Status**: implemented 2026-07-22 (mode-aware script, nivuus-cpu-latency + nivuus-cpu-mode@ units, hooks wired, `intel_idle.max_cstate=3` removed from the default BLS entry). Reboot-pending: verification steps 3/4/6/9 (deep C-states, idle package power, boot resolving to idle) — all require the cmdline change to take effect. Live-verified: mode switching, PM QoS constraint (250 µs), EPP persistence.

## Problem

The host CPU policy is static. `optimize-cpu-thermal.sh` applies one setting at
boot (P-cores EPP `balance_performance`, E-cores EPP `power`, E-cores capped at
2000 MHz) and never changes it, whether the Windows gaming VM owns the P-cores
or the machine is idle.

Two different workloads want two different policies:

- **VM running** — the guest pins vCPUs to the P-cores. Frequency ramp latency
  and scaling jitter are what hurt a game, so the P-cores should react as fast
  as possible.
- **VM off** — the host runs Docker/HA/ollama on all 24 threads. This box has
  very limited cooling (fans have no headroom), so idle should be cool, quiet
  and genuinely cheap.

### The idle half cannot be delivered by EPP and frequency alone

Measured on 2026-07-22 on a quiesced host (26.3 °C ambient, VM off):

```
package power            12.7 W
CPU%c1   40-52 %         cores sit in shallow C1E
CPU%c6   11-18 %
CPU%c7    0.00 %         never reached
Pkg%pc2   0.00 %         the package NEVER enters an idle state
Pkg%pc6   0.00 %
available states: POLL (0 us), C1E (2 us), C6 (220 us) — and nothing deeper
```

The cause is on the kernel command line: **`intel_idle.max_cstate=3`**. It caps
idle at C6 and hides C8/C10 entirely, on P-cores and E-cores alike. Package
C-states require every core to be in a deep state simultaneously, so they are
never entered at all — and that is where the real idle savings live on Alder
Lake. `CHANGELOG.md:26` shows the parameter was already loosened once, from
`max_cstate=1` to `3`.

EPP and frequency caps only bite *under load*. On an idle host they buy almost
nothing: the machine already measures 12.7 W. **The entire idle gain is in the
C-states, which the original version of this design did not address.**

This is the same architectural mistake as `isolcpus=0-15`, retired on
2026-07-22: a *boot-time* parameter constraining the machine for its whole
uptime to serve a condition that only holds while the VM runs. It was almost
certainly added for passthrough — deep C-states cause audio crackle and jitter
in VFIO guests — which is a real requirement, but only when a guest exists.

## Goals

- Switch the CPU policy automatically on VM start and VM stop.
- Make the VM-off state genuinely low-power, not merely less boosted.
- Keep one owner for the CPU knobs. No second tuning daemon.
- Leave the calibrated thermal contract untouched.

## Non-goals

- **Changing RAPL.** PL1 = 50 W / 4 s and PL2 = 58 W stay identical in both
  modes. They were calibrated on 2026-07-17 and re-measured on 2026-07-22;
  they must not move without re-testing temperatures.
- **Reviving `tuned`.** See `2026-07-22-cpu-perf-consolidation-design.md`.
- Touching the fan curve, which is temperature-driven and mode-agnostic.

## Design

Three levers, applied together by mode.

### Lever 1 and 2 — EPP and frequency (unchanged)

`optimize-cpu-thermal.sh` takes an optional mode argument:

| Invocation | Effect |
| --- | --- |
| `optimize-cpu-thermal.sh` | Full apply: RAPL + fan curve + policy. Mode auto-detected via `LC_ALL=C virsh domstate Windows` (`running` → `gaming`, anything else → `idle`), falling back to `idle` if libvirtd does not answer. |
| `optimize-cpu-thermal.sh gaming` | Policy only. |
| `optimize-cpu-thermal.sh idle` | Policy only. |

| Mode | P-cores (0-15) max | P-cores EPP | E-cores (16-23) max | E-cores EPP |
| --- | --- | --- | --- | --- |
| `gaming` | 5100 MHz | `performance` | 2000 MHz | `power` |
| `idle` | 3600 MHz | `power` | 2000 MHz | `power` |
| *(current)* | *5100 MHz* | *`balance_performance`* | *2000 MHz* | *`balance_performance`* |

The governor stays `powersave` in both modes — the correct governor for
`intel_pstate`; EPP is the actual lever. The current row shows E-cores at
`balance_performance` rather than the `power` the script writes: PPD overwrites
it every boot. **The consolidation spec is a prerequisite for this one** —
without it, any EPP this design sets is at the mercy of the next PPD event.

**Expected effect, stated honestly**: under a 50 W package cap the sustained
frequency is power-bound, so `performance` does not buy sustained GHz. It buys
ramp-up speed and less scaling jitter.

### Lever 3 — C-states via PM QoS

Remove `intel_idle.max_cstate=3` from the kernel command line so the deep
states exist at all, then constrain them *dynamically* instead:
`/dev/cpu_dma_latency` is a system-wide PM QoS knob — a process opens it, writes
an int32 of microseconds, and the constraint holds until the descriptor closes.
A C-state is usable only if its exit latency is below the constraint. This is
the mechanism tuned's `force_latency` used.

| Mode | PM QoS constraint | Deepest usable state |
| --- | --- | --- |
| `gaming` | ~250 us (see below) | C6 — reproduces today's proven-good behaviour |
| `idle` | none (default 2000000000 us) | C8/C10 and package PC2/PC6 |

Gaming mode is deliberately **not** more aggressive than today. Today's machine
runs the VM fine with C1E + C6 available; the constraint simply recreates that
ceiling once the cap is lifted, so the VM's latency environment is unchanged and
only the idle case improves.

The 250 us figure is provisional. After removing the cmdline parameter, read
every `/sys/devices/system/cpu/cpu0/cpuidle/state*/latency` and pick a threshold
strictly between C6's latency (220 us today) and the next state's. Do not guess:
C8/C10 latencies are not observable while the parameter is in place.

Held by `/usr/local/bin/nivuus-cpu-latency` (opens the device, writes the value,
then blocks) under `nivuus-cpu-latency.service` (`Type=exec`,
`Restart=on-failure`). `gaming` starts the unit, `idle` stops it. Closing the
descriptor is what lifts the constraint, so the unit's lifetime *is* the policy
— there is no state to reset and nothing to clean up after a crash.

### Kernel command line

Drop `intel_idle.max_cstate=3` from the default BLS entry. This touches the ESP,
which is the fragile path documented in CLAUDE.md: systemd-boot with
hand-managed BLS entries *and* kernelstub, on a 511 MB ESP at ~83 % full. Edit
the BLS entry, then verify with `bootctl list` that the default entry carries
the expected cmdline before rebooting. Keep the 6.12.43 entry untouched as the
rollback path, as was done for `isolcpus`.

### Triggers

A systemd template unit `nivuus-cpu-mode@.service` (`Type=oneshot`) runs
`optimize-cpu-thermal.sh %i`, which also starts or stops
`nivuus-cpu-latency.service`. The two existing, already-validated libvirt hooks
call it:

- `qemu.d/Windows/prepare/begin/10-cpu-confine.sh` → `systemctl start nivuus-cpu-mode@gaming.service`
- `qemu.d/Windows/release/end/10-cpu-release.sh` → `systemctl start nivuus-cpu-mode@idle.service`

At boot, the existing `cpu-thermal-optimization.service` does the full apply,
which resolves to `idle` because the VM is off (`autostart` is disabled;
wake-on-demand starts it).

**Why the indirection through systemd, and not a direct call:** the AppArmor
profile `/etc/apparmor.d/usr.sbin.libvirtd` grants `/etc/libvirt/hooks/** rmix`.
The `ix` means hooks run *inheriting the libvirtd profile*, which allows exec of
`/bin/*`, `/sbin/*`, `/usr/bin/*` and `/usr/sbin/*` (all `PUx`) but **not**
`/usr/local/bin/*` or `/usr/local/sbin/*`. `optimize-cpu-thermal.sh` lives in
`/usr/local/bin`, so a hook calling it directly would die with
`/bin/bash: bad interpreter: Permission denied` and **no AppArmor DENIED line in
dmesg** — the exact failure that broke the CPU-partitioning hook on 2026-07-22.
`/usr/bin/systemctl` is allowed, and the unit it starts runs outside the
confinement. The same reasoning covers `nivuus-cpu-latency`.

### Error handling

- The hooks keep their `exit 0`: a CPU policy failure must never block a VM start.
- sysfs writes keep their existing `|| true`.
- The hook wrappers append stdout/stderr to `/var/log/libvirt-cpu-hook.log`.
  `exit 0` masks failures from the libvirt dispatcher, which logs `code 0`
  regardless, so that file is the only place a silent failure surfaces.
- An unknown mode argument is a usage error: exit non-zero without touching any
  sysfs file.
- If `nivuus-cpu-latency.service` fails to start in `gaming`, log it and
  continue: the guest gets deeper C-states than ideal, which is a jitter
  regression, not an outage.

## Verification

1. `optimize-cpu-thermal.sh gaming` then `idle` by hand: read back
   `energy_performance_preference` and `scaling_max_freq` on cpu0 and cpu16.
2. RAPL `constraint_0/1_power_limit_uw` unchanged across both.
3. After the cmdline change and a reboot, confirm the deep states exist:
   `ls /sys/devices/system/cpu/cpu0/cpuidle/` shows more than three states.
4. In `idle`, `turbostat --show CPU%c7,Pkg%pc2,Pkg%pc6` must become non-zero.
   Today all three read 0.00 — that is the whole point of the change.
5. Measure idle package power before and after against the 12.7 W baseline of
   2026-07-22, on an equally quiesced host (Tdarr paused — see
   `scripts/thermal-campaign/campaign-metadata.md`).
6. In `gaming`, confirm `CPU%c7` returns to 0 and the deepest state used is C6.
7. Start the VM, confirm EPP flips to `performance` via the hook and that
   `nivuus-cpu-latency.service` is active; stop it, confirm the return to
   `power`, 3600 MHz, and the unit stopped.
8. Under a real gaming session, listen for audio crackle and check for frame
   pacing jitter — the symptoms `max_cstate` was originally added to prevent.
9. Reboot: confirm the boot path resolves to `idle`.

## Risks

- **Deep C-states degrade the guest.** This is why the parameter existed. The
  design keeps gaming at today's exact ceiling, so the risk is confined to a
  failure of `nivuus-cpu-latency.service` — hence `Restart=on-failure` and
  verification step 7.
- **The ESP edit.** systemd-boot with two coexisting mechanisms on a nearly full
  ESP; a botched entry means an unbootable default. Verify with `bootctl list`
  before rebooting and keep the 6.12.43 entry as the escape hatch.
- **Sharper bursts** in `gaming`: `performance` reaches high frequency faster.
  PL2 = 58 W bounds the peak and PL1 = 50 W / 4 s the sustained draw, so runaway
  remains impossible.
- **Host sluggishness at idle** from the 3600 MHz cap on bursty host work
  (container builds, HA restarts). One constant to raise if it bites.
- **Hook ordering.** The libvirt dispatcher runs hooks in unordered `find(1)`
  order, so the mode switch may land before or after the cpuset change. The two
  are independent.

## Rollback

Run `optimize-cpu-thermal.sh` with no argument to restore the full calibrated
state, stop `nivuus-cpu-latency.service`, and remove the `systemctl start` lines
from the two hooks. To undo the C-state change, put `intel_idle.max_cstate=3`
back on the BLS entry and reboot. Nothing persists across a reboot beyond the
script, the two unit files, and the cmdline.
