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
# drift. Repair it by label first (which persists across rebuilds), or by
# size as a fallback.
if (-not (Test-Path 'D:\')) {
    # Try to find an unlettered partition with label "Data" — the most reliable
    # discriminator, since the answer file labels it and rebuilds preserve labels.
    $part = Get-Partition | Where-Object {
        -not $_.DriveLetter -and $_.AccessPaths -match 'Data'
    } | Select-Object -First 1

    if (-not $part) {
        # Fallback: select the largest unlettered partition if it meets size.
        # Use -ge to match the validation below, so a 100 GiB partition is accepted.
        $part = Get-Partition | Where-Object {
            -not $_.DriveLetter -and $_.Size -ge ($MinDataGiB * 1GB)
        } | Sort-Object -Property Size -Descending | Select-Object -First 1
        if (-not $part) { throw 'no unlettered volume large enough to be D:' }
        Write-Host "D: assignment: using size heuristic (label not found)"
    }
    else {
        Write-Host "D: assignment: using label-based detection"
    }
    Write-Host "assigning D: to partition $($part.PartitionNumber)"
    Set-Partition -InputObject $part -NewDriveLetter D
}

# Poll for the volume to appear after assignment, with a 10-second timeout.
# Set-Partition is generally synchronous, but enumeration is not guaranteed.
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
