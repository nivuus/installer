#!/bin/bash
# Tests for scripts/hw-blackbox.py against a fake hwmon tree.
#
# The recorder exists to capture a failure that cannot be reproduced on demand
# (an instant platform halt), so its drift alerting and its rotation have to be
# proven here rather than in production.
# Run: scripts/tests/test_hw_blackbox.sh

set -uo pipefail

BB="$(cd "$(dirname "$0")/.." && pwd)/hw-blackbox.py"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

CHIP="$TMP/sys/hwmon2"
mkdir -p "$CHIP" "$TMP/sys/hwmon0" "$TMP/sys/hwmon1"
echo acpitz   > "$TMP/sys/hwmon0/name"
echo coretemp > "$TMP/sys/hwmon1/name"
echo nct6798  > "$CHIP/name"

set_rail() { echo "$2" > "$CHIP/in${1}_input"; }
for i in 0 1 4; do set_rail "$i" 1000; done
echo 49000 > "$CHIP/temp1_input"
echo 69000 > "$CHIP/temp8_input"
echo 75000 > "$CHIP/temp11_input"
echo 1185  > "$CHIP/fan1_input"
echo 1638  > "$CHIP/fan2_input"
echo 1000000 > "$TMP/energy_uj"

# The NVMe chip sorts *after* nct6798 here, so resolving it proves the lookup is
# by name and not by whichever index happens to come first.
mkdir -p "$TMP/sys/hwmon3"
echo nvme  > "$TMP/sys/hwmon3/name"
echo 59000 > "$TMP/sys/hwmon3/temp1_input"

# /proc/interrupts is per-CPU columns that must be summed, and carries plenty of
# lines that look similar (MCP vs MCE) — hence a realistic fake.
cat > "$TMP/interrupts" <<'EOF'
           CPU0       CPU1       CPU2
  9:        123        456        789   IO-APIC    9-fasteoi   acpi
 TRM:        967        967        974   Thermal event interrupts
 THR:          0          0          0   Threshold APIC interrupts
 MCE:          0          0          0   Machine check exceptions
 MCP:          5          6          6   Machine check polls
EOF

mkdir -p "$TMP/edac"   # present but with no controller, like this board

export NIVUUS_HWMON_NAME=nct6798
export NIVUUS_NVME_HWMON_NAME=nvme
export NIVUUS_INTERRUPTS="$TMP/interrupts"
export NIVUUS_EDAC="$TMP/edac"
export NIVUUS_RAPL="$TMP/energy_uj"
export NIVUUS_BLACKBOX="$TMP/bb.csv"
export NIVUUS_BLACKBOX_STATE="$TMP/state/baseline.json"
export NIVUUS_BLACKBOX_INTERVAL=0.02
export NIVUUS_BLACKBOX_BASELINE=5

PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }
check()        { case "$3" in *"$2"*) ok "$1";; *) bad "$1" "$3";; esac; }
check_absent() { case "$3" in *"$2"*) bad "$1" "$3";; *) ok "$1";; esac; }

# The module reads HWMON at import time from a module-level constant, so the
# fake root is injected by patching the constant before exec (see runner above).
export BB_PATH="$BB" FAKE_HWMON="$TMP/sys" FAKE_CHIP="$CHIP"
sample() { python3 -c '
import os, sys, importlib.util
spec = importlib.util.spec_from_file_location("bb", os.environ["BB_PATH"])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.HWMON = os.environ["FAKE_HWMON"]
sys.argv = ["bb"] + sys.argv[1:]
sys.exit(mod.main())
' "$@" 2>&1; }

echo "== chip discovery by name, not index =="
out=$(sample once)
check "finds nct6798 among three chips" "peci" "$out"
check "records every rail present"      "in0,in1,in4" "$out"
check "reports PECI in degrees"         "69.0" "$out"
check "reports fan RPM"                 "1185,1638" "$out"

echo "== the CPU/disk/memory columns are present =="
check "NVMe temperature resolved by name" "59.0" "$out"
check "machine-check + thermal counters"  "mce,mcp,thr,trm" "$out"
check "sums MCP across CPUs (5+6+6)"      ",17," "$out"
check "sums TRM across CPUs (967x2+974)"  "2908" "$out"
check "memory-error columns exist"        "edac_ce,edac_ue" "$out"
check "memory errors read as blank here"  ",," "$(sed -n 2p <<<"$out")"

echo "== a rising machine-check count is reported =="
sed -i 's/^ MCE:          0          0          0/ MCE:          0          0          1/' "$TMP/interrupts"
out2=$(timeout 5 python3 -c '
import os, sys, importlib.util, threading
spec = importlib.util.spec_from_file_location("bb", os.environ["BB_PATH"])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.HWMON = os.environ["FAKE_HWMON"]
threading.Timer(0.4, lambda: os.system("sed -i \"s/^ MCE:          0          0          1/ MCE:          0          0          9/\" " + os.environ["NIVUUS_INTERRUPTS"])).start()
threading.Timer(1.0, lambda: os._exit(0)).start()
sys.argv = ["bb", "run"]
mod.main()
' 2>&1 >/dev/null)
check "flags the machine-check rise" "MCE counter rose 1 -> 9" "$out2"
sed -i 's/^ MCE:          0          0          9/ MCE:          0          0          0/' "$TMP/interrupts"
rm -f "$TMP/bb.csv"*

echo "== fail-open: unknown chip must not crash =="
out=$(NIVUUS_HWMON_NAME=nosuchchip sample once)
rc=$?
check "explains that nothing is recorded" "not found" "$out"
[ "$rc" = 0 ] && ok "exits 0 without a chip" || bad "exits 0 without a chip" "rc=$rc"

echo "== fail-open: a vanished sensor leaves an empty field =="
mv "$CHIP/fan2_input" "$TMP/fan2.away"
out=$(sample once)
check "still emits a row" "in0" "$out"
mv "$TMP/fan2.away" "$CHIP/fan2_input"

echo "== drift alerting is baseline-relative, not mapping-dependent =="
rm -rf "$TMP/state" "$TMP/bb.csv"
timeout 5 python3 -c '
import os, sys, importlib.util, threading
spec = importlib.util.spec_from_file_location("bb", os.environ["BB_PATH"])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.HWMON = os.environ["FAKE_HWMON"]
threading.Timer(1.5, lambda: os._exit(0)).start()
sys.argv = ["bb", "run"]
mod.main()
' >/dev/null 2>"$TMP/err1"
[ -s "$NIVUUS_BLACKBOX_STATE" ] && ok "learns and persists a baseline" \
                               || bad "learns and persists a baseline" "pas de fichier"
check_absent "stays quiet while rails are steady" "drifted" "$(cat "$TMP/err1")"

echo "== the baseline records a band, not just a median =="
python3 -c '
import json, sys
ref = json.load(open(sys.argv[1]))
assert all(isinstance(v, dict) and {"med","lo","hi"} <= set(v) for v in ref.values()), ref
print("ok")' "$NIVUUS_BLACKBOX_STATE" >/dev/null 2>&1 \
  && ok "each rail stores med + lo + hi" \
  || bad "each rail stores med + lo + hi" "$(cat "$NIVUUS_BLACKBOX_STATE")"

echo "== a rail swinging inside its own band is not drift =="
# Relearn with a rail that legitimately swings, the way Vcore does under load.
#
# LE BALANCEMENT AVANCE D UN CRAN PAR LECTURE DU PRODUIT, JAMAIS SUR L HORLOGE.
# Il tournait dans un fil demon toutes les 10 ms pendant que l echantillonneur
# lisait toutes les 20 ms (NIVUUS_BLACKBOX_INTERVAL) : deux horloges, un cycle
# de SIX valeurs, donc un BATTEMENT. Une phase malheureuse faisait sauter
# l indice 3 - le pic a 870 - pendant les 40 echantillons d apprentissage, la
# bande s arretait a 780, et le premier 870 vu ensuite se lisait comme une
# derive de 11,5 %. Le test echouait alors une fois sur huit (mesure du
# 2026-09-05, 8 tours), en accusant un produit qui avait parfaitement raison :
# face a une bande 620-780, un 870 EST hors bande.
#
# Cadencer le stimulus sur les lectures rend la couverture DETERMINISTE - 40
# echantillons sur un cycle de 6 voient chaque phase au moins six fois - et
# rend le test PLUS fort : il verifie desormais vraiment ce qu il affirme,
# c est-a-dire qu une bande apprise sur tout le balancement ne crie pas dessus.
# Le produit n est pas touche : le vrai read_int est appele, le vrai fichier
# est ouvert et relu. Seule la DATE du stimulus change.
rm -rf "$TMP/state" "$TMP/bb.csv"
NIVUUS_BLACKBOX_BASELINE=40 timeout 6 python3 -c '
import os, sys, importlib.util, threading, itertools
spec = importlib.util.spec_from_file_location("bb", os.environ["BB_PATH"])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.HWMON = os.environ["FAKE_HWMON"]
chip = os.environ["FAKE_CHIP"]
swing = itertools.cycle([620, 700, 780, 870, 780, 700])
_real = mod.read_int
def read_int(path):
    if path.endswith("in0_input"):
        open(chip + "/in0_input", "w").write(str(next(swing)) + "\n")
    return _real(path)
mod.read_int = read_int
threading.Timer(2.5, lambda: os._exit(0)).start()
sys.argv = ["bb", "run"]
mod.main()
' >/dev/null 2>"$TMP/err_band"
# LA PRECONDITION AVANT L ABSENCE, et c est le second defaut de ce bloc. Une
# assertion d absence est verte quand le produit se comporte bien ET quand il
# n a jamais tourne : mesure du 2026-09-05, un BB_PATH casse faisait planter le
# python et "raises no alert" passait au vert sur une trace de pile. On exige
# donc d abord que la fenetre d apprentissage ait VU tout le balancement -
# faute de quoi l absence d alerte ne prouverait rien.
python3 -c '
import json, sys
r = json.load(open(sys.argv[1]))["in0"]
sys.exit(0 if r["lo"] <= 620 and r["hi"] >= 870 else 1)' "$NIVUUS_BLACKBOX_STATE" 2>/dev/null \
  && ok "the learning window observed the whole swing" \
  || bad "the learning window observed the whole swing" "$(cat "$NIVUUS_BLACKBOX_STATE" 2>/dev/null)"
check_absent "a wide-swinging rail raises no alert" "rail in0 drifted" "$(cat "$TMP/err_band")"
python3 -c '
import json, sys
r = json.load(open(sys.argv[1]))["in0"]; band = r["hi"] - r["lo"]
sys.exit(0 if band > 100 else 1)' "$NIVUUS_BLACKBOX_STATE" \
  && ok "learns a wide band for a swinging rail" \
  || bad "learns a wide band for a swinging rail" "$(cat "$NIVUUS_BLACKBOX_STATE")"

echo "== a sagging rail is reported =="
rm -rf "$TMP/state" "$TMP/bb.csv"
set_rail 0 1000
timeout 5 python3 -c '
import os, sys, importlib.util, threading
spec = importlib.util.spec_from_file_location("bb", os.environ["BB_PATH"])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.HWMON = os.environ["FAKE_HWMON"]
threading.Timer(1.5, lambda: os._exit(0)).start()
sys.argv = ["bb", "run"]
mod.main()
' >/dev/null 2>&1
set_rail 1 900   # -10 % from the learned 1000
timeout 5 python3 -c '
import os, sys, importlib.util, threading
spec = importlib.util.spec_from_file_location("bb", os.environ["BB_PATH"])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.HWMON = os.environ["FAKE_HWMON"]
threading.Timer(1.0, lambda: os._exit(0)).start()
sys.argv = ["bb", "run"]
mod.main()
' >/dev/null 2>"$TMP/err2"
check "names the drifting rail"   "rail in1 drifted" "$(cat "$TMP/err2")"
check "quantifies the deviation"  "10.0%" "$(cat "$TMP/err2")"
n=$(grep -c "rail in1 drifted" "$TMP/err2")
[ "$n" = 1 ] && ok "alerts once, not every sample" \
             || bad "alerts once, not every sample" "$n alertes"

echo "== the log is a parseable CSV with a header =="
head -1 "$NIVUUS_BLACKBOX" | grep -q "^ts,in0" && ok "header written once" \
                                               || bad "header written once" "$(head -1 "$NIVUUS_BLACKBOX")"
cols=$(head -1 "$NIVUUS_BLACKBOX" | tr ',' '\n' | wc -l)
row=$(sed -n 2p "$NIVUUS_BLACKBOX" | tr ',' '\n' | wc -l)
[ "$cols" = "$row" ] && ok "rows match the header width" \
                     || bad "rows match the header width" "$cols vs $row"

echo "== rotation keeps the log bounded =="
rm -f "$TMP/bb.csv"*
NIVUUS_BLACKBOX_MAX=400 timeout 5 python3 -c '
import os, sys, importlib.util, threading
spec = importlib.util.spec_from_file_location("bb", os.environ["BB_PATH"])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.HWMON = os.environ["FAKE_HWMON"]
threading.Timer(1.5, lambda: os._exit(0)).start()
sys.argv = ["bb", "run"]
mod.main()
' >/dev/null 2>&1
[ -f "$TMP/bb.csv.1" ] && ok "rotates past the size ceiling" \
                       || bad "rotates past the size ceiling" "pas de bb.csv.1"
big=$(find "$TMP" -name 'bb.csv*' -size +2k | wc -l)
[ "$big" = 0 ] && ok "no file grows unbounded" || bad "no file grows unbounded" "$big fichiers"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
