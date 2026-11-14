#!/bin/bash
# Tests for scripts/disk-maintenance.sh, against a fake tree and fake tools.
#
# `--dry-run` is the first thing to run when / fills up, so it must delete
# nothing - the process-accounting archives included, which are also forensic
# evidence. And the Docker build-cache ceiling (25 GB) must be read in bytes:
# `docker system df` prints decimal human sizes (512MB, 26.5GB, 1.2TB), and a
# comparison that only strips "GB" reads a 512 MB cache as 512 and a 1.2 TB
# cache as 1.
set -uo pipefail
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/disk-maintenance.sh"
# Tripwire, as in the WAN suites: without both seams the script would clean
# the real caches and call the real docker. A script lacking them is never run.
for seam in NIVUUS_TEST_PATH NIVUUS_DISK_ROOT; do
    grep -q "$seam" "$SCRIPT" ||
        { echo "REFUSING: $SCRIPT ignores $seam; running it would act on the real host"; exit 1; }
done
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }

B="$TMP/bin"; mkdir -p "$B"
for tool in apt-get uv logger; do
    printf '#!/bin/sh\necho "%s $*" >> "%s/calls"\n' "$tool" "$TMP" > "$B/$tool"
done
cat > "$B/docker" <<EOF
#!/bin/sh
echo "docker \$*" >> "$TMP/calls"
case "\$1" in
    info) exit 0 ;;
    system) echo "Images 3.2GB"; echo "Build Cache \$(cat "$TMP/cache_size")" ;;
esac
EOF
chmod +x "$B"/*

# A fake root holding one stale item for every class the script prunes.
make_tree() {
    R="$TMP/root.$RANDOM"
    mkdir -p "$R/var/log/account" "$R/root/.cache/uv/.tmpabandoned" \
        "$R/home/mallanic/.vscode-server/data/User/globalStorage/saoudrizwan.claude-dev/checkpoints/old-task"
    : > "$R/var/log/account/pacct.30.gz"
    touch -d '40 days ago' "$R/var/log/account/pacct.30.gz" "$R/root/.cache/uv/.tmpabandoned" \
        "$R/home/mallanic/.vscode-server/data/User/globalStorage/saoudrizwan.claude-dev/checkpoints/old-task"
}
run() { NIVUUS_TEST_PATH="$B" NIVUUS_DISK_ROOT="$R" bash "$SCRIPT" "$@" 2>&1; }

# --- dry run: nothing may disappear -----------------------------------------
make_tree; echo 26GB > "$TMP/cache_size"; : > "$TMP/calls"
OUT=$(run --dry-run); RC=$?
[ "$RC" -eq 0 ] && ok "the dry run succeeds" || bad "the dry run succeeds" "rc=$RC $OUT"
[ -e "$R/var/log/account/pacct.30.gz" ] && ok "the dry run keeps the accounting archives" ||
    bad "the dry run keeps the accounting archives" "$OUT"
[ -d "$R/root/.cache/uv/.tmpabandoned" ] && ok "the dry run keeps the uv temporaries" ||
    bad "the dry run keeps the uv temporaries" "$OUT"
[ -d "$R/home/mallanic/.vscode-server/data/User/globalStorage/saoudrizwan.claude-dev/checkpoints/old-task" ] &&
    ok "the dry run keeps the Cline checkpoints" || bad "the dry run keeps the Cline checkpoints" "$OUT"
grep -q "builder prune" "$TMP/calls" && bad "the dry run never prunes Docker" "$(cat "$TMP/calls")" ||
    ok "the dry run never prunes Docker"
echo "$OUT" | grep -q "DRY-RUN: find .*pacct" && ok "the dry run reports the accounting deletion" ||
    bad "the dry run reports the accounting deletion" "$OUT"

# --- real run on the same tree: the stale accounting archive does go --------
: > "$TMP/calls"
OUT=$(run)
[ ! -e "$R/var/log/account/pacct.30.gz" ] && ok "a real run deletes the stale accounting archives" ||
    bad "a real run deletes the stale accounting archives" "$OUT"

# --- build-cache ceiling, decided in bytes ----------------------------------
cache_case() { # <docker size> <expect prune: yes|no>
    make_tree; rm -rf "${R:?}/root" "${R:?}/home"; echo "$1" > "$TMP/cache_size"; : > "$TMP/calls"
    OUT=$(run)
    if grep -q "builder prune" "$TMP/calls"; then got=yes; else got=no; fi
    [ "$got" = "$2" ] && ok "a $1 build cache: prune=$2" || bad "a $1 build cache: prune=$2" "prune=$got $OUT"
}
cache_case 512MB no
cache_case 25GB no
cache_case 25.5GB yes
cache_case 26GB yes
cache_case 1.2TB yes
cache_case 3.4kB no
cache_case 0B no

# An unreadable size must be said, not read as zero.
make_tree; echo "N/A" > "$TMP/cache_size"; : > "$TMP/calls"
OUT=$(run)
echo "$OUT" | grep -q "unreadable" && ok "an unreadable cache size is reported" ||
    bad "an unreadable cache size is reported" "$OUT"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
