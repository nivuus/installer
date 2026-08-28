<#
    Advanced Color (HDR) probe - the acceptance test of sub-project A.

    MUST run in session 1. Over WinRM (session 0) QueryDisplayConfig reports
    zero paths even in the middle of a streaming session, so launch it through
    a scheduled task created with /it, never through winvm directly.

    Budget about three minutes: Add-Type compiles C# on the fly.
#>
param([string]$OutFile = 'C:\nivuus\state\advanced-color.txt')

$ErrorActionPreference = 'Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'AdvancedColor.cs')

New-Item -ItemType Directory -Force -Path (Split-Path $OutFile) | Out-Null
[NivuusAdvancedColor]::Run() | Tee-Object -FilePath $OutFile
