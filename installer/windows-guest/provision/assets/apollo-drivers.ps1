<#
    Helper for stage 25: verify by hardware ID that the two drivers Apollo's
    installer bundles actually ended up present and working - SudoVDA (the
    virtual display) and ViGEmBus (the virtual gamepad bus).

    Split out of 25-apollo.ps1 (which hit the 200-line guideline) rather than
    trimmed down, same call as the config-junction split: this is already a
    coherent, self-contained pair of checks, and cutting along that seam
    keeps their comments intact instead of shortening the ones that earn
    their keep.

    Dot-source this file, then call: Test-ApolloDrivers
#>

function Get-PnpDeviceByHardwareId {
    param(
        [Parameter(Mandatory = $true)][string]$HardwareId,
        [string]$Class
    )
    $lookupParams = @{ PresentOnly = $true; ErrorAction = 'SilentlyContinue' }
    if ($Class) { $lookupParams['Class'] = $Class }
    Get-PnpDevice @lookupParams | Where-Object {
        $hwids = (Get-PnpDeviceProperty -InstanceId $_.InstanceId `
                                        -KeyName 'DEVPKEY_Device_HardwareIds' `
                                        -ErrorAction SilentlyContinue).Data
        $hwids -contains $HardwareId
    } | Select-Object -First 1
}

function Test-ApolloDrivers {
    # Match on the vendor-declared hardware ID, not a ROOT\DISPLAY\*
    # instance-ID wildcard: an instance ID only encodes the INF's enumerator
    # string (e.g. ROOT\DISPLAY\0003) and would also match an unrelated
    # root-enumerated display device. From SudoVDA.inf:
    # %DeviceName%=SudoVDA_Install, Root\SudoMaker\SudoVDA - keep this exact,
    # do not loosen it back to a wildcard.
    $vda = Get-PnpDeviceByHardwareId -Class Display -HardwareId 'Root\SudoMaker\SudoVDA'
    if (-not $vda -or $vda.Status -ne 'OK') {
        throw 'no working SudoVDA device (hardware ID Root\SudoMaker\SudoVDA): SudoVDA did not install'
    }
    Write-Host "SudoVDA OK: $($vda.InstanceId)"

    # Same reasoning, same trap: match ViGEmBus by its own hardware ID, from
    # ViGEmBus.inf: %ViGEmBus.DeviceDesc%=ViGEmBus_Device,
    # Nefarius\ViGEmBus\Gen1 - the modern ID, verified against the real .inf,
    # NOT the pre-2018 Root\ViGEmBus (renamed upstream alongside the vendor's
    # own rename to Nefarius Software Solutions; upstream commit 3a3f4188,
    # "Changed hardware ID from Root\ViGEmBus to Nefarius\ViGEmBus\Gen1").
    # Apollo bundles a modern build, so Root\ViGEmBus would never match a
    # single real install. This is the driver that turns Moonlight's input
    # stream into an XInput/DualShock pad; without it the failure is silent
    # by construction - the image arrives, the sound arrives, and the
    # controller just does nothing, with nothing in Apollo's own logs
    # pointing at a driver. This check is unconditional: it has nothing to do
    # with retrogaming, it is what makes ANY gamepad work over Moonlight at
    # all.
    #
    # Unlike SudoVDA, this is NOT fatal. A missing display leaves nothing to
    # look at, so there is no point continuing. A missing ViGEmBus leaves
    # image and sound working: the operator can still connect over Moonlight
    # and still reach the guest over WinRM (open from stage 00 onward), which
    # is exactly what is needed to diagnose and reinstall the driver.
    # Throwing here would blank an otherwise-healthy console over the one
    # thing that can still be fixed remotely once it is visible - so warn
    # loudly instead, the same call 25-apollo.ps1 already makes for owner
    # config changes in Backup-IfChanged.
    $vigem = Get-PnpDeviceByHardwareId -HardwareId 'Nefarius\ViGEmBus\Gen1'
    if (-not $vigem -or $vigem.Status -ne 'OK') {
        Write-Host 'WARNING: no working ViGEmBus device (hardware ID Nefarius\ViGEmBus\Gen1): no gamepad will work over Moonlight - continuing so the console stays reachable for diagnosis'
    }
    else {
        Write-Host "ViGEmBus OK: $($vigem.InstanceId)"
    }
}
