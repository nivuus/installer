#!/bin/bash
# Nivuus disk maintenance — bounds the caches that were found growing without limit
# during the 2026-07-27 audit (root filesystem had reached 78%).
#
# Deployed as /usr/local/sbin/nivuus-disk-maintenance.sh, run weekly by
# nivuus-disk-maintenance.timer. Every action is idempotent and safe to re-run.
#
# Usage: nivuus-disk-maintenance.sh [--dry-run]

set -uo pipefail

# Test seams, empty in production: NIVUUS_TEST_PATH puts fake tools ahead of
# the real ones, NIVUUS_DISK_ROOT prefixes every path this script cleans.
PATH=${NIVUUS_TEST_PATH:+$NIVUUS_TEST_PATH:}/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ROOT="${NIVUUS_DISK_ROOT:-}"

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

log() { echo "[$(date '+%F %T')] $*"; }

# Report freed space by diffing the used blocks of / around an action.
used_kb() { df -P --block-size=1K / | awk 'NR==2 {print $3}'; }

run() {
    if [ "$DRY_RUN" = 1 ]; then
        log "DRY-RUN: $*"
    else
        "$@"
    fi
}

START_KB=$(used_kb)
log "=== maintenance disque Nivuus — / à $(df -Ph / | awk 'NR==2 {print $5}') ==="

# --- 1. uv leaves a ~130 MB .tmp* directory behind every interrupted operation.
# 448 of them (54 GB) had accumulated over four months. They are never reused.
UV_CACHE="$ROOT/root/.cache/uv"
if [ -d "$UV_CACHE" ]; then
    n=$(find "$UV_CACHE" -maxdepth 1 -name '.tmp*' -type d -mtime +2 2>/dev/null | wc -l)
    if [ "$n" -gt 0 ]; then
        log "uv: suppression de $n temporaires abandonnés (>2 jours)"
        run find "$UV_CACHE" -maxdepth 1 -name '.tmp*' -type d -mtime +2 -exec rm -rf {} +
    fi
    # Drop cache entries no longer reachable from any installed environment.
    command -v uv >/dev/null 2>&1 || export PATH="$PATH:/root/.local/bin"
    command -v uv >/dev/null 2>&1 && run uv cache prune >/dev/null 2>&1
fi

# --- 2. apt keeps every downloaded .deb forever; autoclean only drops the
# packages that are no longer downloadable, which is almost none of them.
APT_KB=$(du -sk "$ROOT/var/cache/apt/archives" 2>/dev/null | cut -f1)
if [ "${APT_KB:-0}" -gt 1048576 ]; then   # > 1 GiB
    log "apt: cache à $((APT_KB / 1024)) Mo, nettoyage"
    run apt-get clean
fi

# --- 3. Cline (VS Code) creates one shadow git repo per task, unbounded.
# A single checkpoint had reached 42 GB.
CLINE="$ROOT"/home/mallanic/.vscode-server/data/User/globalStorage/saoudrizwan.claude-dev/checkpoints
if [ -d "$CLINE" ]; then
    n=$(find "$CLINE" -maxdepth 1 -mindepth 1 -type d -mtime +30 2>/dev/null | wc -l)
    if [ "$n" -gt 0 ]; then
        log "cline: suppression de $n checkpoints de plus de 30 jours"
        run find "$CLINE" -maxdepth 1 -mindepth 1 -type d -mtime +30 -exec rm -rf {} +
    fi
fi

# --- 4. pip and npm caches grow without bound on a build-heavy host.
for cache in "$ROOT/root/.cache/pip" "$ROOT/root/.npm/_cacache" "$ROOT/root/.npm/_npx"; do
    [ -d "$cache" ] || continue
    kb=$(du -sk "$cache" 2>/dev/null | cut -f1)
    if [ "${kb:-0}" -gt 5242880 ]; then   # > 5 GiB
        log "cache: $cache à $((kb / 1024)) Mo, purge"
        run rm -rf "${cache:?}"/*
    fi
done

# --- 5. Process accounting writes ~200 MB/day and rotation keeps everything.
# Through run() like every other deletion: these archives are also forensic
# evidence, and --dry-run is what gets run first when / fills up.
if [ -d "$ROOT/var/log/account" ]; then
    run find "$ROOT/var/log/account" -name 'pacct.*.gz' -mtime +14 -delete
fi

# --- 6. Docker build cache. The daemon-level GC (builder.gc in daemon.json)
# only applies from the next daemon restart, so enforce the ceiling here too.
# `docker system df` prints decimal human sizes (go-units HumanSize: B, kB,
# MB, GB, TB...), so the size is converted to bytes with numfmt --from=si
# before it is compared; LC_ALL=C so the decimal point parses whatever the
# host locale says.
BUILD_CACHE_MAX_BYTES=25000000000   # 25 GB
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    cache_size=$(docker system df --format '{{.Type}} {{.Size}}' 2>/dev/null |
        awk '/^Build Cache / {print $3}')
    suffix=${cache_size%B}
    if cache_bytes=$(LC_ALL=C numfmt --from=si "${suffix^^}" 2>/dev/null); then
        if [ "$cache_bytes" -gt "$BUILD_CACHE_MAX_BYTES" ]; then
            log "docker: build cache at ${cache_size}, pruning entries unused for over 7 days"
            run docker builder prune --force --filter 'until=168h' >/dev/null 2>&1
        fi
    else
        log "docker: unreadable build cache size '${cache_size}', not pruned"
        logger -t nivuus-disk-maintenance -p daemon.warning \
            "Unreadable Docker build cache size '${cache_size}'"
    fi
fi

END_KB=$(used_kb)
FREED_MB=$(((START_KB - END_KB) / 1024))
log "=== terminé — ${FREED_MB} Mo libérés, / à $(df -Ph / | awk 'NR==2 {print $5}') ==="

# Warn loudly if the filesystem is still filling up despite maintenance.
PCT=$(df -P / | awk 'NR==2 {gsub(/%/, "", $5); print $5}')
if [ "$PCT" -ge 85 ]; then
    log "ALERTE: / est à ${PCT}% malgré la maintenance — investigation requise"
    logger -t nivuus-disk-maintenance -p daemon.warning \
        "Root filesystem at ${PCT}% after maintenance"
fi
exit 0
