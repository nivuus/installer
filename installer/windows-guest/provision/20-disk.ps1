<#
    Stage 20: the persistent volume.

    D: is what makes this box rebuildable: Steam, its session and Apollo's
    pairings live there and survive a reinstall of C:. This stage is therefore
    the one that must refuse to continue when D: is not the volume it thinks
    it is - everything after it writes into D:, and a wrong guess would eat
    hundreds of gigabytes of games.
#>
param([Parameter(Mandatory = $true)][string]$PayloadRoot)

$ErrorActionPreference = 'Stop'

$MinDataGiB = 100
$DataMarker = 'D:\state\NIVUUS-DATA.id'

# The answer file assigns D: on a fresh install. On a rebuild the letter can
# drift, or - since both provisioning ISOs stay attached throughout, and one
# of the optical drives can grab D: on first boot of the new C: - be occupied
# outright by something that is not our data partition. Repair
# unconditionally rather than only when D:\ is free: search for the correct
# partition by label first (persists across rebuilds), or by size as a
# fallback, and if D: is occupied by something else, move that occupant out
# of the way instead of aborting.
$currentD = Get-Volume -DriveLetter D -ErrorAction SilentlyContinue
$dIsOurData = $currentD -and $currentD.FileSystem -eq 'NTFS' -and $currentD.FileSystemLabel -eq 'Data'

if (-not $dIsOurData) {
    # Search every partition that is not currently D: (lettered or not) for
    # one whose volume carries our label. Use -eq for exact match (not
    # -match regex) to avoid false positives like "Database".
    $part = Get-Partition | Where-Object { $_.DriveLetter -ne 'D' } | ForEach-Object {
        $vol = Get-Volume -Partition $_ -ErrorAction SilentlyContinue
        if ($vol -and $vol.FileSystem -eq 'NTFS' -and $vol.FileSystemLabel -eq 'Data') { $_ }
    } | Select-Object -First 1

    if ($part) {
        Write-Host "D: assignment: using label-based detection (label='Data'), partition $($part.PartitionNumber) on disk $($part.DiskNumber)"
    }
    else {
        # Fallback: the largest unlettered partition that meets size, in case
        # the label was somehow lost. Use -ge to match the validation below,
        # so a 100 GiB partition is accepted.
        $part = Get-Partition | Where-Object {
            -not $_.DriveLetter -and $_.Size -ge ($MinDataGiB * 1GB)
        } | Sort-Object -Property Size -Descending | Select-Object -First 1
        # No suitable partition at all: nothing to repair onto, refuse.
        if (-not $part) { throw 'no unlettered volume large enough to be D: (and no volume labelled Data found either)' }
        Write-Host "D: assignment: using size heuristic (label not found), partition $($part.PartitionNumber) on disk $($part.DiskNumber)"
    }

    if ($currentD) {
        # D: is occupied by something that is not our data volume - in
        # practice an optical drive, since both ISOs stay attached for this
        # stage to read its own payload from. Move the occupant aside rather
        # than aborting: pick a free letter high in the alphabet so it can
        # never collide with a fixed volume.
        $used = @((Get-Volume -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter }).DriveLetter)
        $freeLetter = $null
        foreach ($code in 90..69) {
            # 90='Z' downto 69='E': never hands out A-D.
            $candidate = [char]$code
            if ($candidate -notin $used) { $freeLetter = $candidate; break }
        }
        if (-not $freeLetter) { throw 'no free drive letter available to relocate the D: occupant' }

        $occupantLabel = if ($currentD.FileSystemLabel) { $currentD.FileSystemLabel } else { '(no label)' }
        Write-Host "D: is occupied by '$occupantLabel' [$($currentD.FileSystem)] - moving it to ${freeLetter}: to free D: for the data partition"
        # Partitions on optical drives are not exposed by Get-Partition/
        # Set-Partition, so relocate through the volume itself via CIM -
        # this works uniformly whether the occupant is optical or a stray
        # fixed volume.
        $occupant = Get-CimInstance -ClassName Win32_Volume -Filter "DriveLetter='D:'"
        if (-not $occupant) { throw 'could not resolve the D: occupant via CIM to relocate it' }
        Set-CimInstance -InputObject $occupant -Property @{ DriveLetter = "${freeLetter}:" } | Out-Null
    }

    Write-Host "assigning D: to partition $($part.PartitionNumber) on disk $($part.DiskNumber)"
    Set-Partition -InputObject $part -NewDriveLetter D
}

# Poll for the volume to appear/settle after (re)assignment, with a
# 10-second timeout. Set-Partition is generally synchronous, but enumeration
# is not guaranteed. Also re-reads $vol below when D: was already correct.
$deadline = (Get-Date).AddSeconds(10)
$vol = $null
while ((Get-Date) -lt $deadline) {
    $vol = Get-Volume -DriveLetter D -ErrorAction SilentlyContinue
    if ($vol) { break }
    Start-Sleep -Milliseconds 500
}
if (-not $vol) { throw "D: drive not found in volume enumeration after 10 seconds" }
if ($vol.FileSystem -ne 'NTFS') {
    throw "D: is $($vol.FileSystem), expected NTFS - wrong volume?"
}
$sizeGiB = [math]::Round($vol.Size / 1GB)
if ($sizeGiB -lt $MinDataGiB) {
    throw "D: is only $sizeGiB GiB; the games partition needs at least $MinDataGiB GiB"
}
Write-Host "D: is $sizeGiB GiB NTFS"

New-Item -ItemType Directory -Force -Path 'D:\state' | Out-Null
New-Item -ItemType Directory -Force -Path 'D:\Steam' | Out-Null

# Seed if absent, never rewrite: this marker is post-hoc detection that D: is
# the correct volume. Windows Setup has already repartitioned before this stage
# runs, so the marker is not a pre-flight guard. On a rebuild, this ID proves
# that D: is the volume a previous provisioning created.
if (-not (Test-Path $DataMarker)) {
    Set-Content -Path $DataMarker -Encoding ASCII -Value @(
        'nivuus_data=1',
        "created=$(Get-Date -Format o)"
    )
    Write-Host 'D: initialised (first install)'
}
else {
    # Read the marker file and extract the created line explicitly (not by indexing,
    # which would read characters from a single-line file).
    $content = @(Get-Content $DataMarker -ErrorAction SilentlyContinue)
    $createdLine = if ($content.Count -ge 2) { $content[1] } else { '(corrupted)' }
    Write-Host "D: carries an existing Nivuus marker: $createdLine"
}
