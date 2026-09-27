#!/bin/bash
# Build the anti-detection QEMU the console's domain runs on.
#
# WHY A SEPARATE QEMU. Debian's qemu-system-x86 announces itself to the
# guest everywhere: "QEMU" in the SMBIOS and ACPI tables, "QEMU keyboard"
# and "QEMU HARDDISK" as device names, the KVM signature in CPUID, the VM
# bit in the boot graphics record. zhaodice/qemu-anti-detection is a
# source patch that renames all of it; it exists only as a patch against
# upstream tarballs, so this script builds upstream QEMU with it applied.
#
# WHY /opt AND NOT /usr/local. The upstream README installs into /usr/local,
# which SHADOWS Debian's binaries for every caller of `qemu-system-x86_64`
# on PATH - libvirt's own capability cache included. An isolated prefix
# leaves the package-managed QEMU untouched (the README itself insists one
# must remain: its runtime dependencies are what the built binary loads)
# and lets the domain name the binary it wants through <emulator>.
#
# The prefix and its stamp file are the contract with qemu_build.py, which
# decides whether this script needs to run at all. Keep them in sync.
set -euo pipefail

QEMU_VERSION="10.2.2"
QEMU_SHA256="784b296ff29c1417aa72323abcb2d2ea9ab9771724f577dcd785c3b04f21e176"
QEMU_URL="https://download.qemu.org/qemu-${QEMU_VERSION}.tar.xz"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="${HERE}/qemu-${QEMU_VERSION}.patch"
PREFIX="${QEMU_PREFIX:-/opt/qemu-anti-detection}"
WORKDIR="${QEMU_BUILD_WORKDIR:-/var/lib/nivuus/qemu-build}"
JOBS="${QEMU_BUILD_JOBS:-$(nproc)}"
STAMP="${PREFIX}/nivuus-build.stamp"

usage() {
    cat >&2 <<USAGE
usage: $(basename "$0") [--prefix DIR] [--workdir DIR] [--jobs N]
Environment: QEMU_PREFIX, QEMU_BUILD_WORKDIR, QEMU_BUILD_JOBS.
USAGE
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="$2"; STAMP="${PREFIX}/nivuus-build.stamp"; shift 2 ;;
        --workdir) WORKDIR="$2"; shift 2 ;;
        --jobs) JOBS="$2"; shift 2 ;;
        *) usage ;;
    esac
done

[ -f "$PATCH" ] || { echo "patch not found: $PATCH" >&2; exit 1; }
PATCH_SHA256="$(sha256sum "$PATCH" | cut -d' ' -f1)"
EXPECTED_STAMP="qemu=${QEMU_VERSION} patch=${PATCH_SHA256}"

if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$EXPECTED_STAMP" ] \
   && [ -x "${PREFIX}/bin/qemu-system-x86_64" ]; then
    echo "already built: ${EXPECTED_STAMP} in ${PREFIX}"
    exit 0
fi

mkdir -p "$WORKDIR"
TARBALL="${WORKDIR}/qemu-${QEMU_VERSION}.tar.xz"
if [ ! -f "$TARBALL" ] || ! echo "${QEMU_SHA256}  ${TARBALL}" | sha256sum -c --status; then
    echo "downloading ${QEMU_URL}"
    curl -fsSL --retry 3 -o "${TARBALL}.part" "$QEMU_URL"
    mv "${TARBALL}.part" "$TARBALL"
fi
# The checksum is pinned, not fetched: a tarball swapped on the mirror
# must fail here, before a single line of it is compiled.
echo "${QEMU_SHA256}  ${TARBALL}" | sha256sum -c --status \
    || { echo "checksum mismatch on ${TARBALL}" >&2; exit 1; }

SRC="${WORKDIR}/qemu-${QEMU_VERSION}"
# Always from a fresh tree: applying a patch twice, or on a tree a previous
# failed run left half-patched, is exactly what `patch` cannot untangle.
rm -rf "$SRC"
tar -xJf "$TARBALL" -C "$WORKDIR"
echo "applying $(basename "$PATCH")"
patch -d "$SRC" -p1 --forward --silent < "$PATCH"

# One target, no docs, no tools the domain does not use. Rust is off
# because the package's apt list carries no Rust toolchain and the patch
# touches none of it. Everything the production domain needs is
# built in: KVM, VNC (jpeg/png for the local console), TPM (swtpm stays
# external), vhost-user-fs for virtiofs, seccomp for libvirt's sandbox,
# numa for the hugepage pinning, libusb for a passed-through USB device.
cd "$SRC"
./configure \
    --prefix="$PREFIX" \
    --target-list=x86_64-softmmu \
    --enable-kvm --enable-vnc --enable-vnc-jpeg --enable-png \
    --enable-tpm --enable-seccomp --enable-numa --enable-libusb \
    --enable-cap-ng --enable-attr --enable-virtfs \
    --disable-rust --disable-docs --disable-tools --disable-guest-agent \
    --disable-gtk --disable-sdl --disable-spice --disable-werror \
    --disable-user
make -j"$JOBS"
make install

echo "$EXPECTED_STAMP" > "$STAMP"
# The source tree is 1.5 GiB once built; the tarball (140 MiB) stays so a
# rebuild after a patch change does not depend on the network.
rm -rf "$SRC"
echo "installed: ${EXPECTED_STAMP} in ${PREFIX}"
"${PREFIX}/bin/qemu-system-x86_64" --version | head -1
