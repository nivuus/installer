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
# drift, so repair it from the volume label rather than assume it.
if (-not (Test-Path 'D:\')) {
    # Use -not $_.DriveLetter (not $_.DriveLetter -eq $null) because Get-Partition
    # returns an unassigned partition's DriveLetter as [char]0, not $null.
    # -not is true for both, so this correctly matches unlettered partitions.
    $part = Get-Partition | Where-Object {
        -not $_.DriveLetter -and $_.Size -gt ($MinDataGiB * 1GB)
    } | Sort-Object -Property Size -Descending | Select-Object -First 1
    if (-not $part) { throw 'no unlettered volume large enough to be D:' }
    Write-Host "assigning D: to partition $($part.PartitionNumber)"
    Set-Partition -InputObject $part -NewDriveLetter D
}

$vol = Get-Volume -DriveLetter D
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
    Write-Host "D: carries an existing Nivuus marker: $((Get-Content $DataMarker)[1])"
}
