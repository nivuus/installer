#!/bin/bash
# Build the Nivuus installer ISO with live-build.
#
# Syncs the installer code and the Nivuus repo into the live image's
# includes.chroot tree, then runs `lb config` + `lb build`. Run as root.
set -euo pipefail

ISO_BUILD_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALLER_DIR="$(dirname "$ISO_BUILD_DIR")"          # installer/
REPO_DIR="$(dirname "$INSTALLER_DIR")"               # repo root (packages/installer/)
# The MQTT agent lives in its own repository since 2026-08-26. In the standard
# working layout it sits beside this one, as packages/mqtt. Override with
# MQTT_REPO_DIR if it is checked out elsewhere.
MQTT_REPO_DIR="${MQTT_REPO_DIR:-$(dirname "$REPO_DIR")/mqtt}"
INCLUDES="${ISO_BUILD_DIR}/config/includes.chroot"
PAYLOAD_INSTALLER="${INCLUDES}/opt/nivuus-installer"
PAYLOAD_SRC="${INCLUDES}/opt/nivuus-src"

if [ "$(id -u)" -ne 0 ]; then
    echo "E: live-build must run as root (sudo $0)" >&2
    exit 1
fi

command -v lb >/dev/null 2>&1 || {
    echo "E: live-build not installed. Run: apt-get install live-build" >&2
    exit 1
}

echo "==> Cleaning previous build artefacts"
cd "$ISO_BUILD_DIR"
lb clean --purge >/dev/null 2>&1 || true

# Populate the payloads from git-TRACKED files only (via `git archive`), never
# the raw working tree. This is a security guarantee: untracked/gitignored files
# — logs (mqtt/agent.log), machine-local Claude settings (.claude), build
# artifacts — can hold real secrets and must NEVER reach the public image. Only
# committed (scrubbed) content ships. Falls back to rsync outside a git repo.
if git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "==> Exporting tracked repo -> opt/nivuus-src (git archive)"
    rm -rf "$PAYLOAD_SRC"; mkdir -p "$PAYLOAD_SRC"
    git -C "$REPO_DIR" archive HEAD | tar -x -C "$PAYLOAD_SRC"

    echo "==> Exporting tracked installer -> opt/nivuus-installer (git archive)"
    rm -rf "$PAYLOAD_INSTALLER"; mkdir -p "$PAYLOAD_INSTALLER"
    git -C "$REPO_DIR" archive HEAD installer \
        | tar -x -C "$PAYLOAD_INSTALLER" --strip-components=1
    # The runtime installer payload doesn't need the ISO build tree.
    rm -rf "$PAYLOAD_INSTALLER/iso-build"
else
    echo "==> (no git repo) Syncing working tree with exclusions"
    mkdir -p "$PAYLOAD_INSTALLER" "$PAYLOAD_SRC"
    rsync -a --delete --exclude 'iso-build' --exclude '__pycache__' \
        --exclude '*.pyc' "${INSTALLER_DIR}/" "${PAYLOAD_INSTALLER}/"
    rsync -a --delete --exclude '.git' --exclude '.claude' --exclude 'node_modules' \
        --exclude 'dist' --exclude '*.deb' --exclude '*.log' \
        --exclude 'installer/iso-build' --exclude '__pycache__' \
        "${REPO_DIR}/" "${PAYLOAD_SRC}/"
fi

# Optional: build the MQTT agent .deb so the engine can install it offline.
# The agent is a sibling repository (nivuus/mqtt), not part of this one.
if [ "${BUILD_MQTT_DEB:-0}" = "1" ]; then
    if [ -f "${MQTT_REPO_DIR}/package.json" ]; then
        echo "==> Building MQTT agent .deb from ${MQTT_REPO_DIR}"
        ( cd "${MQTT_REPO_DIR}" && npm install && npm run package:deb ) || \
            echo "W: MQTT .deb build failed; continuing without it"
        mkdir -p "${PAYLOAD_SRC}/mqtt"
        cp "${MQTT_REPO_DIR}"/*.deb "${PAYLOAD_SRC}/mqtt/" 2>/dev/null || true
    else
        # Never fail silently here: BUILD_MQTT_DEB=1 is an explicit request.
        echo "W: BUILD_MQTT_DEB=1 but no MQTT repo at ${MQTT_REPO_DIR}" >&2
        echo "W: clone https://github.com/nivuus/mqtt or set MQTT_REPO_DIR" >&2
    fi
fi

# Nivuus packages: sibling repositories embedded as payload, discovered at
# install time from /opt/nivuus-packages/*/nivuus-package.yaml. Same mechanism
# as the MQTT .deb above - a sibling repo, exported from its TRACKED files
# only, never the raw working tree.
PAYLOAD_PACKAGES="${INCLUDES}/opt/nivuus-packages"
rm -rf "$PAYLOAD_PACKAGES"
for pkg_repo in ${PACKAGE_REPOS:-}; do
    if [ ! -f "${pkg_repo}/nivuus-package.yaml" ]; then
        echo "W: ${pkg_repo} has no nivuus-package.yaml; skipping" >&2
        continue
    fi
    pkg_name=$(basename "$pkg_repo")
    echo "==> Exporting package ${pkg_name} -> opt/nivuus-packages/${pkg_name}"
    mkdir -p "${PAYLOAD_PACKAGES}/${pkg_name}"
    if git -C "$pkg_repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git -C "$pkg_repo" archive HEAD | tar -x -C "${PAYLOAD_PACKAGES}/${pkg_name}"
    else
        rsync -a --exclude '.git' --exclude '__pycache__' \
            "${pkg_repo}/" "${PAYLOAD_PACKAGES}/${pkg_name}/"
    fi
done

echo "==> Configuring live-build"
./auto/config

echo "==> Building ISO (this takes a while)…"
lb build

ISO=$(ls -1 "${ISO_BUILD_DIR}"/*.iso 2>/dev/null | head -1 || true)
if [ -n "$ISO" ]; then
    echo "==> Done: $ISO"
else
    echo "W: build finished but no .iso found in ${ISO_BUILD_DIR}" >&2
fi
