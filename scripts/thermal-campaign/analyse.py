#!/usr/bin/env python3
"""Summarise a thermal campaign CSV.

Reports the steady state (second half of each scenario) rather than the whole
window: the first half is the thermal ramp, and the question the campaign
answers is about equilibrium, not transients.
"""
import csv
import collections
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "/home/mallanic/Projects/Nivuus/scripts/thermal-campaign/campaign.csv"

ORDER = [
    "s0_idle", "s1_cpu", "cooldown_after_s1_cpu",
    "s2_igpu", "cooldown_after_s2_igpu",
    "s3_gpu", "cooldown_after_s3_gpu",
    "s4_cpu_gpu", "cooldown_after_s4_cpu_gpu",
]

rows = list(csv.DictReader(open(PATH)))
groups = collections.defaultdict(list)
for r in rows:
    groups[r["label"]].append(r)

hdr = (f"{'scenario':<26}{'pkgW':>7}{'pkgC':>6}{'maxC':>6}{'MHz':>7}"
       f"{'SYSTIN':>8}{'fan1':>7}{'fan2':>7}{'gpuC':>6}{'gpuW':>7}{'load':>7}")
print(hdr)
print("-" * len(hdr))

summary = {}
for key in ORDER:
    if key not in groups:
        continue
    all_rows = groups[key]
    steady = all_rows[len(all_rows) // 2:]          # second half = equilibrium

    def avg(col):
        return sum(float(r[col]) for r in steady) / len(steady)

    peak = max(float(r["pkg_temp_c"]) for r in all_rows)
    summary[key] = {
        "pkg_w": avg("pkg_watt"), "pkg_c": avg("pkg_temp_c"), "max_c": peak,
        "mhz": avg("avg_mhz"), "systin": avg("systin_c"),
        "gpu_c": avg("gpu_temp_c"), "gpu_w": avg("gpu_watt"),
    }
    s = summary[key]
    print(f"{key:<26}{s['pkg_w']:>7.1f}{s['pkg_c']:>6.0f}{peak:>6.0f}"
          f"{s['mhz']:>7.0f}{s['systin']:>8.0f}{avg('fan1_rpm'):>7.0f}"
          f"{avg('fan2_rpm'):>7.0f}{s['gpu_c']:>6.0f}{s['gpu_w']:>7.0f}"
          f"{avg('loadavg1'):>7.1f}")

# --- the question the campaign exists to answer --------------------------

print()
if "s1_cpu" in summary and "s4_cpu_gpu" in summary:
    a, b = summary["s1_cpu"], summary["s4_cpu_gpu"]
    print("DECIDING COMPARISON — same 50 W package budget, hotter case")
    print(f"  case air (SYSTIN) {a['systin']:.0f} C -> {b['systin']:.0f} C "
          f"({b['systin']-a['systin']:+.0f} K)")
    print(f"  package temp      {a['pkg_c']:.0f} C -> {b['pkg_c']:.0f} C "
          f"({b['pkg_c']-a['pkg_c']:+.0f} K)")
    print(f"  package power     {a['pkg_w']:.1f} W -> {b['pkg_w']:.1f} W")
    print()
    print("  A static cap holds power constant, so any package rise here is")
    print("  pure ambient sensitivity — exactly what a closed loop removes.")
