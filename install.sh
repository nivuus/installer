#!/bin/bash
# Nivuus - Cloud Gaming Server Installation Script
# One-line install for complete system configuration

set -e

# NIVUUS_DIR resolves to the script's own directory unless overridden via env.
# The Nivuus installer (install-engine) sets NIVUUS_DIR=/opt/nivuus inside the chroot.
NIVUUS_DIR="${NIVUUS_DIR:-$(cd "$(dirname "$(readlink -f "$0")")" && pwd)}"
INSTALL_DIR="/usr/local/bin"
SYSTEMD_DIR="/etc/systemd/system"

# Non-interactive mode: skip all confirmation prompts (assume "yes").
# Triggered by `--non-interactive`/`-y` flag or NIVUUS_ASSUME_YES=1 env var.
NIVUUS_ASSUME_YES="${NIVUUS_ASSUME_YES:-0}"
for arg in "$@"; do
    case "$arg" in
        --non-interactive|-y|--yes) NIVUUS_ASSUME_YES=1 ;;
    esac
done

echo "========================================"
echo "Nivuus Installation"
echo "Cloud Gaming Server Configuration"
echo "========================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ Please run as root (sudo ./install.sh)"
    exit 1
fi

# Detect CPU architecture
echo "Detecting hardware..."
CPU_MODEL=$(lscpu | grep "Model name" | cut -d':' -f2 | xargs)
echo "  CPU: $CPU_MODEL"

# Check for hybrid CPU (P+E cores)
if ! lscpu | grep -q "Model name.*12900K"; then
    echo "⚠️  Warning: This configuration is optimized for Intel i9-12900K"
    echo "   Your CPU: $CPU_MODEL"
    if [ "$NIVUUS_ASSUME_YES" = "1" ]; then
        echo "   (non-interactive: continuing anyway)"
    else
        read -p "Continue anyway? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
fi

# Install dependencies
echo ""
echo "Installing dependencies..."
apt-get update -qq
apt-get install -y \
    qemu-kvm \
    libvirt-daemon-system \
    libvirt-clients \
    bridge-utils \
    virt-manager \
    ovmf \
    stress-ng \
    lm-sensors \
    python3-pip

echo "✅ Dependencies installed"

# Setup CPU thermal optimization
echo ""
echo "Installing CPU thermal optimization..."
cp "$NIVUUS_DIR/scripts/optimize-cpu-thermal.sh" "$INSTALL_DIR/optimize-cpu-thermal.sh"
chmod +x "$INSTALL_DIR/optimize-cpu-thermal.sh"

# Create systemd service
cat > "$SYSTEMD_DIR/cpu-thermal-optimization.service" <<EOF
[Unit]
Description=CPU Thermal Optimization (RAPL 50W/58W + full turbo P-cores, E-cores 2000MHz powersave)
After=multi-user.target

[Service]
Type=oneshot
ExecStart=$INSTALL_DIR/optimize-cpu-thermal.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cpu-thermal-optimization.service
echo "✅ CPU thermal optimization service installed"

# Setup dynamic host/VM CPU partitioning
echo ""
echo "Installing dynamic CPU partitioning..."
NIVUUS_VM_NAME="${NIVUUS_VM_NAME:-Windows}"

# The script MUST live under /etc/libvirt/hooks/: the AppArmor profile for libvirtd
# grants "/etc/libvirt/hooks/** rmix", so hooks run *inheriting* that profile, which
# allows executing /bin, /sbin, /usr/bin and /usr/sbin - but NOT /usr/local/sbin.
# Installing it there instead fails at VM start with the misleading
# "/bin/bash: bad interpreter: Permission denied".
cp "$NIVUUS_DIR/scripts/vm-cpu-partition.sh" /etc/libvirt/hooks/vm-cpu-partition.sh
chmod +x /etc/libvirt/hooks/vm-cpu-partition.sh

# libvirt hooks: squeeze the host onto the CPUs the VM does not pin while it runs,
# give the whole machine back as soon as it stops. Thin wrappers so the logic stays
# in the repo. The dispatcher passes the domain name as $1.
HOOK_BASE="/etc/libvirt/hooks/qemu.d/$NIVUUS_VM_NAME"
mkdir -p "$HOOK_BASE/prepare/begin" "$HOOK_BASE/release/end"

cat > "$HOOK_BASE/prepare/begin/10-cpu-confine.sh" <<'EOF'
#!/bin/bash
# Confine the host cgroups to the CPUs the VM does not pin, for as long as it runs.
/etc/libvirt/hooks/vm-cpu-partition.sh confine "$1" >> /var/log/libvirt-cpu-hook.log 2>&1
exit 0
EOF

cat > "$HOOK_BASE/release/end/10-cpu-release.sh" <<'EOF'
#!/bin/bash
# Hand every CPU back to the host once the VM is gone (shutdown or hibernation).
/etc/libvirt/hooks/vm-cpu-partition.sh release "$1" >> /var/log/libvirt-cpu-hook.log 2>&1
exit 0
EOF

chmod +x "$HOOK_BASE/prepare/begin/10-cpu-confine.sh" "$HOOK_BASE/release/end/10-cpu-release.sh"
echo "✅ Dynamic CPU partitioning installed (hooks for domain $NIVUUS_VM_NAME)"

# Configure hugepages
echo ""
echo "Configuring hugepages..."
HUGEPAGES_NEEDED=8192  # 16GB / 2MB

if ! grep -q "vm.nr_hugepages" /etc/sysctl.conf; then
    echo "vm.nr_hugepages = $HUGEPAGES_NEEDED" >> /etc/sysctl.conf
    sysctl -p
    echo "✅ Hugepages configured ($HUGEPAGES_NEEDED pages)"
else
    echo "ℹ️  Hugepages already configured"
fi

# Update GRUB for CPU isolation and IOMMU
echo ""
echo "Updating GRUB configuration..."
GRUB_FILE="/etc/default/grub"

# CPU range reserved for the VM and IOMMU type are parameterisable so the generic
# Nivuus installer can pass values computed from the actual hardware (see
# install-engine hardware detection). Defaults match the original i9-12900K.
#
# NOTE: this range is deliberately NOT passed as isolcpus. isolcpus is a boot-time
# parameter, so it would keep those CPUs out of the scheduler for the whole uptime
# and leave the host on the remaining cores even while the VM is shut off. The
# host/VM split is done dynamically instead, by vm-cpu-partition.sh driven from the
# libvirt hooks. Only nohz_full is kept: it removes the timer tick from those CPUs
# while the VM owns them, and costs nothing when the host schedules there.
NIVUUS_ISOLCPUS="${NIVUUS_ISOLCPUS:-0-15}"
NIVUUS_IOMMU="${NIVUUS_IOMMU:-intel_iommu=on iommu=pt}"
# vfio-pci.ids=<vendor:device,...> for GPU passthrough (optional, auto-detected).
NIVUUS_VFIO_IDS="${NIVUUS_VFIO_IDS:-}"

NIVUUS_GRUB_PARAMS="nohz_full=${NIVUUS_ISOLCPUS} ${NIVUUS_IOMMU}"
if [ -n "$NIVUUS_VFIO_IDS" ]; then
    NIVUUS_GRUB_PARAMS="$NIVUUS_GRUB_PARAMS vfio-pci.ids=${NIVUUS_VFIO_IDS}"
fi

# Match either marker: hosts installed before the dynamic-partitioning change
# carry isolcpus= and must not get the parameters appended a second time.
if ! grep -qE "nohz_full=|isolcpus=" "$GRUB_FILE"; then
    # Backup GRUB config
    cp "$GRUB_FILE" "${GRUB_FILE}.nivuus-backup"

    # Get current GRUB_CMDLINE_LINUX_DEFAULT
    CURRENT_CMDLINE=$(grep "^GRUB_CMDLINE_LINUX_DEFAULT" "$GRUB_FILE" | cut -d'"' -f2)

    # Add Nivuus parameters
    NEW_CMDLINE="$CURRENT_CMDLINE $NIVUUS_GRUB_PARAMS"

    # Update GRUB
    sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT=\"$NEW_CMDLINE\"/" "$GRUB_FILE"

    update-grub
    echo "✅ GRUB updated ($NIVUUS_GRUB_PARAMS) (requires reboot)"
    NEEDS_REBOOT=1
else
    echo "ℹ️  GRUB already configured"
fi

# Configure VFIO modules
echo ""
echo "Configuring VFIO modules..."
MODULES_FILE="/etc/modules"

for module in vfio vfio_iommu_type1 vfio_pci; do
    if ! grep -q "^$module$" "$MODULES_FILE"; then
        echo "$module" >> "$MODULES_FILE"
    fi
done

echo "✅ VFIO modules configured"

# Apply thermal optimization immediately (skipped inside a chroot/install image:
# CPU frequency controls under /sys are not writable from the installer chroot;
# the cpu-thermal-optimization.service will apply them on first real boot).
echo ""
if [ "${NIVUUS_IN_CHROOT:-0}" = "1" ]; then
    echo "Skipping immediate thermal optimization (chroot install) — will run on first boot."
else
    echo "Applying CPU thermal optimization..."
    "$INSTALL_DIR/optimize-cpu-thermal.sh" || \
        echo "⚠️  Could not apply thermal optimization now (will apply on next boot)"
fi

echo ""
echo "========================================"
echo "Installation Summary"
echo "========================================"
echo ""
echo "✅ Dependencies installed"
echo "✅ CPU thermal optimization configured"
echo "✅ Hugepages configured (8192 pages)"
echo "✅ GRUB updated (CPU isolation + IOMMU)"
echo "✅ VFIO modules configured"
echo ""

if [ -n "$NEEDS_REBOOT" ]; then
    echo "⚠️  REBOOT REQUIRED to apply kernel parameters"
    echo ""
    echo "After reboot, run:"
    echo "  sudo $NIVUUS_DIR/scripts/validate-install.sh"
else
    echo "ℹ️  No reboot required"
fi

echo ""
echo "Next steps:"
echo "  1. Configure GPU passthrough (edit GRUB vfio-pci.ids)"
echo "  2. Build the Windows guest VM:"
echo "     python3 $NIVUUS_DIR/installer/windows-guest/domain.py define"
echo "  3. Run validation:"
echo "     sudo $NIVUUS_DIR/scripts/validate-install.sh"
echo ""
echo "Documentation: $NIVUUS_DIR/docs/"
echo ""
echo "Nivuus installation complete!"
