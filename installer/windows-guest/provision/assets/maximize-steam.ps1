<#
    Maximize the Steam window inside the streaming session.

    Apollo runs this as a prep-cmd, which is the only place it can work: the
    session-0 tooling (WinRM) cannot touch a session-1 window. Steam restores
    its previous window geometry, which on a fresh virtual display is a small
    window in a corner.
#>
$ErrorActionPreference = 'Continue'

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class Win {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    public const int SW_MAXIMIZE = 3;
}
'@

$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    $p = Get-Process -Name 'steam' -ErrorAction SilentlyContinue |
         Where-Object { $_.MainWindowTitle -eq 'Steam' } | Select-Object -First 1
    if ($p) { [Win]::ShowWindow($p.MainWindowHandle, [Win]::SW_MAXIMIZE) | Out-Null }
    Start-Sleep -Milliseconds 500
}
