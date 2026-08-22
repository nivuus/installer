#!/bin/bash
# Nivuus disk maintenance — bounds the caches that were found growing without limit
# during the 2026-07-27 audit (root filesystem had reached 78%).
#
# Deployed as /usr/local/sbin/nivuus-disk-maintenance.sh, run weekly by
# nivuus-disk-maintenance.timer. Every action is idempotent and safe to re-run.
#
# Usage: nivuus-disk-maintenance.sh [--dry-run]

set -uo pipefail

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
UV_CACHE=/root/.cache/uv
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
APT_KB=$(du -sk /var/cache/apt/archives 2>/dev/null | cut -f1)
if [ "${APT_KB:-0}" -gt 1048576 ]; then   # > 1 GiB
    log "apt: cache à $((APT_KB / 1024)) Mo, nettoyage"
    run apt-get clean
fi

# --- 3. Cline (VS Code) creates one shadow git repo per task, unbounded.
# A single checkpoint had reached 42 GB.
CLINE=/home/mallanic/.vscode-server/data/User/globalStorage/saoudrizwan.claude-dev/checkpoints
if [ -d "$CLINE" ]; then
    n=$(find "$CLINE" -maxdepth 1 -mindepth 1 -type d -mtime +30 2>/dev/null | wc -l)
    if [ "$n" -gt 0 ]; then
        log "cline: suppression de $n checkpoints de plus de 30 jours"
        run find "$CLINE" -maxdepth 1 -mindepth 1 -type d -mtime +30 -exec rm -rf {} +
    fi
fi

# --- 4. pip and npm caches grow without bound on a build-heavy host.
for cache in /root/.cache/pip /root/.npm/_cacache /root/.npm/_npx; do
    [ -d "$cache" ] || continue
    kb=$(du -sk "$cache" 2>/dev/null | cut -f1)
    if [ "${kb:-0}" -gt 5242880 ]; then   # > 5 GiB
        log "cache: $cache à $((kb / 1024)) Mo, purge"
        run rm -rf "${cache:?}"/*
    fi
done

# --- 5. Process accounting writes ~200 MB/day and rotation keeps everything.
find /var/log/account -name 'pacct.*.gz' -mtime +14 -delete 2>/dev/null

# --- 6. Docker build cache. The daemon-level GC (builder.gc in daemon.json)
# only applies from the next daemon restart, so enforce the ceiling here too.
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    cache_gb=$(docker system df --format '{{.Type}} {{.Size}}' 2>/dev/null |
        awk '/Build Cache/ {gsub(/GB/, "", $3); print int($3)}')
    if [ "${cache_gb:-0}" -gt 25 ]; then
        log "docker: build cache à ${cache_gb} Go, purge des entrées inutilisées >7j"
        run docker builder prune --force --filter 'until=168h' >/dev/null 2>&1
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
