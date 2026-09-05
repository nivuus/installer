#!/bin/bash
# Install WinRM CLI for VM communication
# Builds and installs winrm-cli from source

set -e

echo "========================================"
echo "WinRM CLI Installation"
echo "========================================"
echo ""

# Check if already installed
if [[ -f /usr/local/bin/winrm ]]; then
    VERSION=$(/usr/local/bin/winrm --version 2>/dev/null || echo "unknown")
    echo "✅ WinRM CLI already installed ($VERSION)"
    echo ""
    read -p "Reinstall? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
fi

# Check for Go
if ! command -v go &> /dev/null; then
    echo "Installing Go..."
    apt-get update
    apt-get install -y golang-go
fi

GO_VERSION=$(go version | awk '{print $3}')
echo "✅ Go installed: $GO_VERSION"
echo ""

# Clone or update winrm-cli
WINRM_DIR="/home/mallanic/Projects/winrm-cli"

if [[ -d "$WINRM_DIR" ]]; then
    echo "Updating winrm-cli repository..."
    cd "$WINRM_DIR"
    git pull || true
else
    echo "Cloning winrm-cli repository..."
    git clone https://github.com/masterzen/winrm-cli.git "$WINRM_DIR"
    cd "$WINRM_DIR"
fi

echo ""
echo "Building winrm-cli..."
go build -o winrm

echo ""
echo "Installing to /usr/local/bin/winrm..."
install -m 755 winrm /usr/local/bin/winrm

echo ""
echo "✅ WinRM CLI installed successfully!"
echo ""
echo "Next steps:"
echo "  1. Configure WinRM on Windows VM (run setup-winrm.ps1)"
echo "  2. Configure winvm credentials:"
echo ""
echo "     mkdir -p ~/.config/nivuus"
echo "     cat > ~/.config/nivuus/winvm.conf << EOF"
echo "     VM_HOSTNAME=\"192.168.3.2\""
echo "     VM_USERNAME=\"Administrateur\""
echo "     VM_PASSWORD=\"your-password-here\""
echo "     EOF"
echo "     chmod 600 ~/.config/nivuus/winvm.conf"
echo ""
echo "  3. Install winvm wrapper:"
echo "     sudo install -m 755 console/host/winvm /usr/local/bin/winvm"
echo ""
echo "  4. Test connection:"
echo "     winvm \"hostname\""
