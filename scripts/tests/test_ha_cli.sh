#!/bin/bash
# Tests for scripts/ha, the Home Assistant REST CLI, against a fake curl.
#
# Two properties: the long-lived token never appears on curl's command line
# (anything in argv is readable by every local user in /proc/<pid>/cmdline
# while the call runs), and with no token configured the script stops with
# its setup help instead of calling Home Assistant with a placeholder.
#
# HOME points at a scratch directory so the operator's real
# ~/.config/nivuus/ha.conf is never read, and HA_URL at a closed local port
# so even a fake that failed to shadow curl could not reach a real instance.
set -uo pipefail
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/ha"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL — $1"; echo "         got: $2"; }

B="$TMP/bin"; mkdir -p "$B" "$TMP/home"
# The fake records its argv one argument per line, and the content of every
# header file it is handed with -H @file, the way the real curl reads them.
cat > "$B/curl" <<EOF
#!/bin/bash
printf '%s\n' "\$@" > "$TMP/argv"
: > "$TMP/headers"
prev=""
for a in "\$@"; do
    if [[ "\$prev" == "-H" && "\$a" == @* ]]; then cat "\${a#@}" >> "$TMP/headers"; fi
    prev="\$a"
done
echo '[]'
EOF
chmod +x "$B/curl"

TOKEN="test-token-$RANDOM-$RANDOM"
run() { env -u HA_TOKEN HOME="$TMP/home" PATH="$B:$PATH" HA_URL="http://127.0.0.1:9" "$@" bash "$SCRIPT" states; }

OUT=$(run HA_TOKEN="$TOKEN" 2>&1); RC=$?
[ "$RC" -eq 0 ] && ok "a configured call succeeds" || bad "a configured call succeeds" "rc=$RC $OUT"
[ -s "$TMP/argv" ] && ok "the fake curl was called" || bad "the fake curl was called" "no argv recorded"
if [ ! -s "$TMP/argv" ] || grep -qF -- "$TOKEN" "$TMP/argv"; then
    bad "the token is not on curl's command line" "$(cat "$TMP/argv")"
else
    ok "the token is not on curl's command line"
fi
grep -qxF "Authorization: Bearer $TOKEN" "$TMP/headers" &&
    ok "the token still reaches curl as an Authorization header" ||
    bad "the token still reaches curl as an Authorization header" "$(cat "$TMP/headers")"

rm -f "$TMP/argv"
OUT=$(run 2>&1); RC=$?
[ "$RC" -ne 0 ] && ok "no token configured exits non-zero" || bad "no token configured exits non-zero" "rc=$RC"
echo "$OUT" | grep -q "token not configured" &&
    ok "no token configured prints the setup help" || bad "no token configured prints the setup help" "$OUT"
[ ! -e "$TMP/argv" ] && ok "no token configured never calls curl" || bad "no token configured never calls curl" "$(cat "$TMP/argv")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
