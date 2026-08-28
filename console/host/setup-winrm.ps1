# Setup WinRM on Windows VM
# Run this script as Administrator in the Windows VM

Write-Host "========================================"
Write-Host "WinRM Configuration for Nivuus"
Write-Host "========================================"
Write-Host ""

# Check if running as Administrator
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "ERROR: This script must be run as Administrator!" -ForegroundColor Red
    Write-Host "Right-click PowerShell and select 'Run as Administrator'" -ForegroundColor Yellow
    exit 1
}

Write-Host "✓ Running as Administrator" -ForegroundColor Green
Write-Host ""

# Enable WinRM
Write-Host "Enabling WinRM..."
Enable-PSRemoting -Force -SkipNetworkProfileCheck

# Configure WinRM service
Write-Host "Configuring WinRM service..."
Set-Service -Name WinRM -StartupType Automatic
Start-Service -Name WinRM

# Set WinRM configuration
Write-Host "Setting WinRM configuration..."
Set-Item WSMan:\localhost\Service\Auth\Basic -Value $true
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $true

# Configure firewall
Write-Host "Configuring firewall rules..."
New-NetFirewallRule -DisplayName "WinRM HTTP" `
                     -Direction Inbound `
                     -Protocol TCP `
                     -LocalPort 5985 `
                     -Action Allow `
                     -Enabled True `
                     -ErrorAction SilentlyContinue

New-NetFirewallRule -DisplayName "WinRM HTTPS" `
                     -Direction Inbound `
                     -Protocol TCP `
                     -LocalPort 5986 `
                     -Action Allow `
                     -Enabled True `
                     -ErrorAction SilentlyContinue

# Set trusted hosts (allow connection from host)
Write-Host "Configuring trusted hosts..."
Set-Item WSMan:\localhost\Client\TrustedHosts -Value "*" -Force

# Get network adapters and their IPs
Write-Host ""
Write-Host "Network Configuration:"
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike "127.*"} |
    Select-Object IPAddress, InterfaceAlias | Format-Table -AutoSize

# Test WinRM
Write-Host ""
Write-Host "Testing WinRM locally..."
$testResult = Test-WSMan -ComputerName localhost -ErrorAction SilentlyContinue

if ($testResult) {
    Write-Host "✓ WinRM is working!" -ForegroundColor Green
} else {
    Write-Host "✗ WinRM test failed" -ForegroundColor Red
}

# Display connection info
Write-Host ""
Write-Host "========================================"
Write-Host "WinRM Configuration Complete"
Write-Host "========================================"
Write-Host ""
Write-Host "Connection information:"
Write-Host "  Protocol: HTTP"
Write-Host "  Port: 5985"
Write-Host "  Authentication: Basic"
Write-Host ""

# Get primary IP
$primaryIP = (Get-NetIPAddress -AddressFamily IPv4 |
              Where-Object {$_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -eq "Dhcp"} |
              Select-Object -First 1).IPAddress

if ($primaryIP) {
    Write-Host "Test from Linux host with:"
    Write-Host "  winvm `"hostname`"" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Or configure ~/.config/nivuus/winvm.conf:"
    Write-Host "  VM_HOSTNAME=`"$primaryIP`"" -ForegroundColor Cyan
    Write-Host "  VM_USERNAME=`"$env:USERNAME`"" -ForegroundColor Cyan
    Write-Host "  VM_PASSWORD=`"your-password`"" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "SECURITY WARNING:" -ForegroundColor Yellow
Write-Host "  - Basic auth is enabled (credentials sent in clear text)"
Write-Host "  - Unencrypted connections allowed"
Write-Host "  - Only use on trusted private networks!"
Write-Host "  - For production, configure HTTPS with certificates"
Write-Host ""
Write-Host "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
