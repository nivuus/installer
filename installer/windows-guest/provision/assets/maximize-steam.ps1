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

# 180 s, pas 30. Au TOUT PREMIER lancement, Steam telecharge sa propre mise a
# jour avant d ouvrir la moindre fenetre — 242 Mo de paquets mesures le
# 2026-08-26 — et trente secondes expirent largement avant. Le script rendait
# alors la main sans rien avoir maximise, et le client voyait un bureau vide
# avec un Steam bien vivant mais sans fenetre. Ce n est pas un cout : la boucle
# sort des que la fenetre parait, donc l attente longue ne se paie que dans le
# cas ou elle sert.
$deadline = (Get-Date).AddSeconds(180)
while ((Get-Date) -lt $deadline) {
    $p = Get-Process -Name 'steam' -ErrorAction SilentlyContinue |
         Where-Object { $_.MainWindowTitle -eq 'Steam' } | Select-Object -First 1
    if ($p) { [Win]::ShowWindow($p.MainWindowHandle, [Win]::SW_MAXIMIZE) | Out-Null }
    Start-Sleep -Milliseconds 500
}
