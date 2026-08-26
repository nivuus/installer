<#
    Stage 35: mount the host folders the appliance is allowed to see.

    Four TARGETED shares, never a whole filesystem root: virtiofsd runs as root
    and passthrough mode carries no permission net, so whatever is exposed is
    exposed without a filter. Sub-folders keep an incident inside a blast radius
    the owner chose.

    "Console" is this machine's vocabulary - what it is to whoever uses it, not
    what runs inside it. It stays true if the guest ever changes OS, and does
    not collide with the host, which is called Nivuus.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

# Same order as domain.py's SHARES. The letters start at E: because C: is the
# disposable system and D: the games partition.
$Shares = @(
    @{ Tag = 'Downloads';   Letter = 'E'; Label = 'Telechargements' },
    @{ Tag = 'Games';       Letter = 'F'; Label = 'Jeux' },
    @{ Tag = 'Console';     Letter = 'G'; Label = 'Console' },
    @{ Tag = 'ConsoleSave'; Letter = 'H'; Label = 'Sauvegardes Console' }
)

# Optical drives grab the first free letters and would sit exactly where the
# shares belong - measured on 2026-08-26, the two installation media held E: and
# F:. Push them out of the way FIRST; 20-disk.ps1 fights the same battle for D:.
$wanted = $Shares.Letter
Get-Volume | Where-Object { $_.DriveType -eq 'CD-ROM' -and $_.DriveLetter } | ForEach-Object {
    if ($wanted -contains [string]$_.DriveLetter) {
        $free = 90..73 | ForEach-Object { [char]$_ } |
                Where-Object { $_ -notin $wanted -and -not (Test-Path "${_}:") } |
                Select-Object -First 1
        if (-not $free) { throw "no free letter to move optical drive $($_.DriveLetter): out of the way" }
        Get-Partition -DriveLetter $_.DriveLetter -ErrorAction SilentlyContinue |
            Set-Partition -NewDriveLetter $free -ErrorAction SilentlyContinue
        # A CD-ROM is not a partition; mountvol is what actually moves it.
        mountvol "$($_.DriveLetter):" /D 2>&1 | Out-Null
        Write-Host "optical drive $($_.DriveLetter): unmounted to free the letter"
    }
}

$virtiofs = Join-Path $PayloadRoot 'drivers\virtio\viofs\virtiofs.exe'
if (-not (Test-Path $virtiofs)) { throw "missing $virtiofs" }
$installed = 'C:\nivuus\virtiofs.exe'
Copy-Item -Path $virtiofs -Destination $installed -Force

foreach ($s in $Shares) {
    $name = "NivuusShare_$($s.Tag)"
    # One service per share: the packaged VirtioFsSvc mounts a SINGLE tag (on
    # Z:), so four shares need four instances with their own tag and letter.
    sc.exe delete $name 2>&1 | Out-Null
    $bin = "`"$installed`" -t $($s.Tag) -m $($s.Letter):"
    sc.exe create $name binPath= $bin start= auto DisplayName= "Nivuus share $($s.Tag)" 2>&1 | Out-Null
    sc.exe failure $name reset= 86400 actions= restart/5000/restart/10000/restart/30000 2>&1 | Out-Null
    sc.exe start $name 2>&1 | Out-Null
}

# Read back: a service that started is not a share that mounted. virtiofs needs
# the host side present, and a tag with no matching <filesystem> in the domain
# XML fails silently at mount time.
Start-Sleep -Seconds 8
$missing = @()
foreach ($s in $Shares) {
    if (Test-Path "$($s.Letter):\") {
        Write-Host "$($s.Tag) mounted on $($s.Letter): ($($s.Label))"
    } else {
        $missing += "$($s.Tag) -> $($s.Letter):"
    }
}
if ($missing) {
    # Not fatal: a domain without these <filesystem> entries is a legitimate
    # configuration (the A-era test domain has none), and refusing here would
    # strand an otherwise healthy appliance over an optional convenience.
    Write-Host "WARNING: shares not mounted: $($missing -join ', ') - check the domain XML carries a matching <filesystem> tag for each"
}
