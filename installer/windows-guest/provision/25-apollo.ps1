<#
    Stage 25: Apollo, its virtual display, and the configuration that survives
    a rebuild.

    Order is load-bearing. The installer must run before the junction, because
    it creates the config directory; the junction must exist before the
    service starts, or Apollo writes its pairings onto C: where the next
    rebuild would erase them.

    /D= is deliberately NOT passed: NSIS wants it unquoted and last, and the
    default path contains a space that PowerShell would quote. The install
    location is read back from the registry instead of assumed.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'
$StateDir = 'C:\nivuus\state'
$ApolloState = 'D:\state\apollo'

$installer = Get-ChildItem -Path (Join-Path $PayloadRoot 'drivers\apollo') `
                           -Filter '*.exe' | Sort-Object Name | Select-Object -First 1
if (-not $installer) { throw "no Apollo installer in $PayloadRoot\drivers\apollo" }

Write-Host "installing $($installer.Name)"
$proc = Start-Process -FilePath $installer.FullName -ArgumentList '/S' -Wait -PassThru
if ($proc.ExitCode -ne 0) { throw "Apollo installer exited $($proc.ExitCode)" }

$root = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Apollo' `
                          -ErrorAction SilentlyContinue).InstallLocation
if (-not $root) { $root = 'C:\Program Files\Apollo' }
if (-not (Test-Path (Join-Path $root 'sunshine.exe'))) {
    throw "Apollo does not look installed under $root"
}
Write-Host "Apollo installed at $root"

# The bundled SudoVDA package. install.bat seeds its own certificate into Root
# and TrustedPublisher, then removes and recreates the device node, so running
# it is idempotent.
$vdaDir = Join-Path $root 'drivers\sudovda'
if (Test-Path (Join-Path $vdaDir 'install.bat')) {
    $p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'install.bat' `
                       -WorkingDirectory $vdaDir -Wait -PassThru
    Write-Host "SudoVDA install.bat exited $($p.ExitCode)"
}
$vda = Get-PnpDevice -InstanceId 'ROOT\DISPLAY\*' -ErrorAction SilentlyContinue |
       Where-Object { $_.Status -eq 'OK' } | Select-Object -First 1
if (-not $vda) { throw 'no working ROOT\DISPLAY device: SudoVDA did not install' }
Write-Host "SudoVDA OK: $($vda.InstanceId)"

# --- The junction. Stop the service first: it holds its config directory open.
Stop-Service -Name 'ApolloService' -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$config = Join-Path $root 'config'
New-Item -ItemType Directory -Force -Path $ApolloState | Out-Null

$item = Get-Item -Path $config -ErrorAction SilentlyContinue
$isJunction = $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
if (-not $isJunction) {
    if ($item) {
        # Seed if absent, never overwrite: on a rebuild D: already holds the
        # pairings, and the freshly installed config is the empty one.
        foreach ($f in Get-ChildItem -Path $config -Force -ErrorAction SilentlyContinue) {
            $target = Join-Path $ApolloState $f.Name
            if (-not (Test-Path $target)) { Copy-Item -Path $f.FullName -Destination $target -Recurse }
        }
        Remove-Item -Path $config -Recurse -Force
    }
    cmd.exe /c "mklink /J `"$config`" `"$ApolloState`"" | Out-Null
}
$item = Get-Item -Path $config
if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "$config is not a junction: Apollo would write its pairings onto C:"
}
Write-Host "config junctioned to $ApolloState"

# --- Generated files: always rewritten. They carry no user state, and pinning
# them to first-install would strand the appliance on an old configuration.
Copy-Item -Path (Join-Path $PayloadRoot 'config\sunshine.conf') `
          -Destination (Join-Path $ApolloState 'sunshine.conf') -Force
Copy-Item -Path (Join-Path $PayloadRoot 'config\apps.json') `
          -Destination (Join-Path $ApolloState 'apps.json') -Force
New-Item -ItemType Directory -Force -Path 'C:\nivuus\apollo' | Out-Null
Copy-Item -Path (Join-Path $PayloadRoot 'provision\assets\maximize-steam.ps1') `
          -Destination 'C:\nivuus\apollo\maximize-steam.ps1' -Force

# --- Web-manager credentials: seeded only when the state file is absent, so a
# rebuild keeps whatever the owner set. sunshine.exe hashes them itself.
$secrets = Import-PowerShellDataFile -Path (Join-Path $PayloadRoot 'config\secrets.psd1')
if (-not (Test-Path (Join-Path $ApolloState 'sunshine_state.json'))) {
    # The values stay in variables: Start-Transcript records the source line,
    # not the expansion, so the password never lands in provision.log.
    $p = Start-Process -FilePath (Join-Path $root 'sunshine.exe') `
                       -ArgumentList '--creds', $secrets.ApolloUser, $secrets.ApolloPassword `
                       -Wait -PassThru -NoNewWindow
    if ($p.ExitCode -ne 0) { throw "sunshine.exe --creds exited $($p.ExitCode)" }
    Write-Host 'Apollo web credentials seeded'
}
else {
    Write-Host 'Apollo state exists: web credentials left untouched'
}

# --- Service and firewall. Apollo ships the scripts; use them rather than
# reimplement sc/netsh incantations that would drift from the vendor's.
$scripts = Join-Path $root 'scripts'
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'install-service.bat' `
              -WorkingDirectory $scripts -Wait -PassThru | Out-Null
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'autostart-service.bat' `
              -WorkingDirectory $scripts -Wait -PassThru | Out-Null
# Delete first: netsh happily creates a duplicate rule on every rebuild.
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'delete-firewall-rule.bat' `
              -WorkingDirectory $scripts -Wait -PassThru | Out-Null
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'add-firewall-rule.bat' `
              -WorkingDirectory $scripts -Wait -PassThru | Out-Null

Start-Service -Name 'ApolloService' -ErrorAction SilentlyContinue
$svc = Get-Service -Name 'ApolloService'
if ($svc.Status -ne 'Running') { throw "ApolloService is $($svc.Status), expected Running" }
Set-Content -Path (Join-Path $StateDir 'apollo.root') -Value $root -Encoding ASCII
Write-Host 'Apollo running'
