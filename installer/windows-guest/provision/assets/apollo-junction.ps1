<#
    Helper for stage 25: point Apollo's config directory at D:\state\apollo
    via an NTFS junction, so Apollo's pairings survive a rebuild of C:.

    Split out of 25-apollo.ps1 (which hit the 200-line guideline) rather than
    trimmed down: this maneuver was already a coherent, self-contained block
    - the most delicate one in the whole sub-project - and cutting along that
    seam keeps it intact instead of shortening comments that earn their keep.

    Dot-source this file, then call:
        Set-ApolloConfigJunction -Config $config -ApolloState $ApolloState
#>

function Set-ApolloConfigJunction {
    param(
        [Parameter(Mandatory = $true)][string]$Config,
        [Parameter(Mandatory = $true)][string]$ApolloState
    )

    # Stop the service first: it holds its config directory open. Poll for
    # it to actually reach Stopped (same shape as the waits in
    # 15-virtio.ps1 / 20-disk.ps1) instead of guessing a fixed delay - moving
    # a directory the service still holds open would fail in a far more
    # confusing way.
    $svc = Get-Service -Name 'ApolloService' -ErrorAction SilentlyContinue
    if ($svc) {
        Stop-Service -Name 'ApolloService' -Force -ErrorAction SilentlyContinue
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline) {
            $svc = Get-Service -Name 'ApolloService'
            if ($svc.Status -eq 'Stopped') { break }
            Start-Sleep -Milliseconds 500
        }
        if ($svc.Status -ne 'Stopped') {
            throw "ApolloService did not reach Stopped (still $($svc.Status)) after 30 seconds: it may still hold $Config open"
        }
    }

    New-Item -ItemType Directory -Force -Path $ApolloState | Out-Null

    $item = Get-Item -Path $Config -ErrorAction SilentlyContinue
    $isJunction = $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)

    # A junction that merely exists is not enough: it must point at
    # $ApolloState, or every write below lands on D: while Apollo's real
    # (stale) target is what it actually reads. Compare the resolved target,
    # not just the attribute.
    $pointsAtApolloState = $false
    if ($isJunction) {
        $currentTarget = ($item.Target | Select-Object -First 1)
        if ($currentTarget) {
            # On PowerShell 5.1, .Target can come back as the raw NT
            # substitute name (\??\D:\state\apollo) rather than the plain
            # path. Strip that prefix before comparing, or a perfectly
            # correct junction would never match, be re-pointed and
            # mis-logged on every single run.
            $currentTarget = ($currentTarget -replace '^\\\?\?\\', '').TrimEnd('\')
        }
        $pointsAtApolloState = ($currentTarget -eq $ApolloState.TrimEnd('\'))
        if (-not $pointsAtApolloState) { Write-Host "config junction points at '$currentTarget', not '$ApolloState': re-pointing it" }
    }

    if (-not $pointsAtApolloState) {
        if ($item) {
            # Seed if absent, never overwrite: $Config (fresh dir, or a
            # junction pointing somewhere stale) may hold files D: doesn't
            # have yet. Only top-level names are compared - accepted: a
            # nested-only new file would be missed, but that only bites a
            # fresh Apollo version on a rebuilt C: whose D: predates it; an
            # ordinary upgrade already has a correct junction and never
            # reaches here.
            foreach ($f in Get-ChildItem -Path $Config -Force -ErrorAction SilentlyContinue) {
                $seedTarget = Join-Path $ApolloState $f.Name
                if (-not (Test-Path $seedTarget)) { Copy-Item -Path $f.FullName -Destination $seedTarget -Recurse }
            }
            if ($isJunction) {
                # .Delete() unlinks the reparse point only. Remove-Item
                # -Recurse on a junction is a known PS 5.1 trap: it follows
                # the link and deletes the TARGET's contents - real state,
                # at the stale path.
                (Get-Item -Path $Config).Delete()
            }
            else {
                Remove-Item -Path $Config -Recurse -Force
            }
        }
        $mklinkOutput = cmd.exe /c "mklink /J `"$Config`" `"$ApolloState`"" 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "mklink /J failed (exit $LASTEXITCODE) linking '$Config' -> '$ApolloState': $mklinkOutput"
        }
    }
    $item = Get-Item -Path $Config
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$Config is not a junction: Apollo would write its pairings onto C:"
    }
}
