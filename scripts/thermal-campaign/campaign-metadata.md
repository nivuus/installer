# Campaign run metadata — 2026-07-22

Recorded alongside `campaign.csv`. Without the ambient temperature the run is
uninterpretable later: every package temperature in the CSV is an ambient plus
a delta, and only the delta is a property of the machine.

| Field | Value |
| --- | --- |
| Date | 2026-07-22, started 14:54 CEST |
| **Ambient room temperature** | **26.3 °C** (measured, user-reported) |
| Host | Debian 13, kernel 6.12.96+deb13-amd64 |
| Uptime at start | ~3 min (rebooted 14:51:40) |
| Windows VM | shut off for the whole run |
| RAPL | PL1 = 50 W / 4 s, PL2 = 58 W (unchanged, calibrated 2026-07-17) |
| P-cores | max 5100 MHz, EPP `balance_performance` |
| E-cores | max 2000 MHz, EPP `balance_performance` **(not `power`)** |
| Fan curve | nct6798 Smart Fan IV, 35 °C/30 % → 80 °C/100 % |
| `nivuus-ollama` | stopped, so it does not pollute the measurements |

## Aborted first attempt (14:54) — read this before trusting any CSV

The first run was killed after ~3 min and its rows must not be used. The
machine was never idle: **Tdarr was transcoding continuously** —
`mediamanager-tdarr-node-nvenc-1` at 336 % CPU *plus* NVENC on the RTX 4070,
and `mediamanager-tdarr-node-1` at 115 %, six `tdarr-ffmpeg` processes. That
contaminates every scenario, CPU and GPU alike, and the transcode queue differs
between runs, so two runs would never be comparable.

Symptom worth remembering: `s0_idle` averaged 50.2 W (exactly the PL1 cap) at
71 °C with a 80 °C peak, while CLAUDE.md documents "Idle: ≤45 °C". A package
pinned at its power cap during what should be idle is the tell.

## Quiescing procedure (applied for the real run)

Both Tdarr nodes paused through the API rather than stopping the containers, so
no transcode progress is lost:

```bash
for nid in 0P8cfbF6U wG6U978ta; do          # NvencNode, MyInternalNode
  curl -s -X POST http://127.0.0.1:8265/api/v2/update-node \
    -H 'Content-Type: application/json' \
    -d "{\"data\":{\"nodeID\":\"$nid\",\"nodeUpdates\":{\"nodePaused\":true}}}"
done
```

Pausing blocks new jobs but lets running ones finish; the four in flight were
allowed to drain (longest ETA 13 min) instead of being cancelled.

> **RESTORE AFTER THE CAMPAIGN** — same call with `"nodePaused":false`.
> Leaving the nodes paused silently stops all media transcoding.

`nivuus-ollama` is also stopped for the run and must be restarted afterwards
(`docker compose -f /opt/nivuus/ollama/docker-compose.yml up -d ollama`).

## Plex could not be quiesced — what that costs

Plex was running a library-wide **"Detecting Credits"** pass at ~630 % CPU. It
could not be stopped cleanly: the activity reports `cancellable="0"`, it is not
a butler task (those only run between `ButlerStartHour=2` and
`ButlerEndHour=5`), and no global preference exposes it. Only a container
restart would have killed it, which was out of scope.

The run therefore proceeds with Plex active. What that does and does not break:

- **Valid**: every temperature and power figure. S1–S4 saturate the package at
  the 50 W PL1 cap whether Plex runs or not, and the question being answered —
  "50 W gives what temperature at 26.3 °C ambient?" — is physics, indifferent
  to which process spends the watts.
- **Contaminated**: sustained frequency (Plex steals cycles, so the measured
  MHz is a floor, not the true figure) and the idle baseline. Measure
  `s0_idle_clean` separately on a genuinely quiet machine.

The first aborted attempt is kept as `aborted-tdarr-contaminated.csv` — never
merge it with `campaign.csv`, its labels are identical but its conditions are
not.

## Unrelated anomaly spotted while quiescing

`grocy` sits at ~72 % CPU continuously. That is abnormal for a grocery manager
— most likely a runaway cron or import loop. It burns RAPL budget permanently
on a machine that has none to spare. Not investigated; worth a separate look.

**E-core EPP is wrong during this run.** `optimize-cpu-thermal.sh` sets E-cores
to EPP `power`, but `power-profiles-daemon` overwrites all 24 threads with
`balance_performance` — both units start at the same second (14:52:13 on this
boot). The campaign therefore measures the machine *as it actually runs today*,
not as the script intends. Any re-run after the PPD removal must be compared
against this one, not merged with it.

## Why this campaign exists

To decide whether the static RAPL cap (PL1 = 50 W) should stay, or be replaced
by `thermald` running a closed loop on package temperature. The static cap was
calibrated on 2026-07-17 at an unrecorded ambient; a cap tuned at one room
temperature is wrong at another, whereas a closed loop compensates. The
deciding scenario is `s4_cpu_gpu`: the RTX 4070 dumping ~145 W into a compact
case is the worst realistic ambient, and it is exactly where a static cap
calibrated in cooler conditions would overshoot.
