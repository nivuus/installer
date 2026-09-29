# Console guest: Gaming Services, session shell, PROVISION_VERSION

* **Gaming Services (the Store's `9MWPM2CQNLHN`) is installed by default, and
  kept current, by `provision/33-winget.ps1` + `34-gaming-services.ps1` —
  added 2026-08-30.** IoT Enterprise LTSC ships **no Microsoft Store**, and
  GDK titles (including Steam's Forza/Sea of Thieves/Halo MCC) refuse to
  launch without this package. Measured on the production guest that day:
  winget installed offline (bundle + `License1.xml` + the three x64
  frameworks, all pinned to `fetch_payload.WINGET_VERSION`) reaches the
  Store through its `msstore` source on this SKU — `winget install --id
  9MWPM2CQNLHN --source msstore` succeeded and left
  `Microsoft.GamingServices 38.116.6003.0` with `GamingServices` and
  `GamingServicesNet` both Running. Re-measured end to end on 2026-09-04 with
  the split assets deployed: all four Store packages resolve, the services are
  re-asserted and read back, and a second immediate run skips the Store
  (`Store non interroge : dernier controle il y a 0,0 h`) while still redoing
  the services. **Bumping `WINGET_VERSION` alone used to change nothing**:
  `fetch()` keeps whatever already exists and all three winget artefacts land
  on version-free paths, so a populated `drivers/` shipped the OLD winget in
  silence and the TOFU manifest could not see it (same path, same digest).
  `prune_stale_winget()`/`stamp_winget()` close that, mirroring
  `prune_stale_retro`. Three things that are not obvious:
  (1) **`Add-AppxPackage` returns `0x80070005` over WinRM** — the network
  logon lacks the AppX deployment service's rights, so replaying either
  stage from the host needs `schtasks /it` in session 1, exactly like the
  screenshot trick; provisioning itself is unaffected, it already runs in
  session 1. (2) **An App Installer bundle laid down without its frameworks
  installs with NO error and leaves no `winget.exe`** — hence the ordering
  and the "run `winget --version`" check in stage 33, both test-guarded.
  (3) **`winget install` IS the update path** (it upgrades a package already
  present) and returns `0x8A15002B` / `-1978335189` when already current —
  the normal case, not a failure. Nothing on a Store-less SKU ever updates
  this package on its own, so the `gaming-services-refresh` scheduled task
  (logon +2 min, daily 04:00, one real check per 20 h) replays that single
  command; **stage 34 never fails provisioning** — same arbitration as
  ViGEmBus and the retro emulators, and the task repairs a bad day by
  itself. Durable witness: `D:\state\gaming-services.txt`.

* **`explorer.exe` is the session shell again, and Gaming Services alone was
  never enough — both settled 2026-08-30 on the production guest.** Forza
  Horizon 6 (Steam) hung on its splash for 15 min, **black screen with no
  message at all**, `UI_SCENE=Splash`, working set pinned at ~2.2 GB,
  `TOTAL_LAUNCHES=0` — with `Microsoft.GamingServices` installed and both its
  services Running. Two causes, and the second is the one that mattered:
  1. **The chain, not the component.** `XboxIdentityProvider` (which *displays*
     the Xbox sign-in dialog), `GamingApp` and `WindowsStore` were all absent.
     `assets/xbox-stack.ps1` (dot-sourced by `gaming-services.ps1`, which kept
     the throttle/log/witness) now installs all four Store ids and puts
     `wlidsvc`/`XblAuthManager`/`XboxNetApiSvc`/`XblGameSave`/`LicenseManager`
     in **`Automatic`** — LTSC ships them `Manual` and their demand-start
     triggers do not fire here, so they were `Stopped` after every reboot.
     `ClipSVC` is handled apart: it is started, never reconfigured.
     **Two corrections, measured on the guest 2026-09-04.** (a) The claim that
     `Set-Service` on `ClipSVC` returns `Access is denied` **was not
     reproduced** — it simply was not reconfigured, and no error was seen; the
     handling is still right (protected service, and it does not need
     `Automatic`), but the reason written here was not the measured one.
     (b) **`XblGameSave` is no longer forced to `Automatic` at all** — it is
     started and left alone, exactly like `ClipSVC`. `Set-Service` does not
     throw and the start type goes back to `Manual` (registry `Start` = 3),
     seen on three of four runs; `sc qtriggerinfo XblGameSave` shows a
     `NETWORK EVENT / RPC INTERFACE EVENT`, so **Windows re-manages this
     service and that is it working as designed, not a fault**. Forcing it
     meant fighting the OS for a witness that would read `chaine-incomplete`
     **permanently** — and a witness that is always red stops being read, so
     the next real defect in the chain would go unnoticed. `Manual` +
     trigger-start is now recorded in the code as the **expected** state for
     those two; what is verified is that they are `Running`. Measured after the
     change, with `XblGameSave` forced back to `Manual` first: witness
     `status=ok`, `Start=3`, `Status=Running`. The four services that *do* hold
     `Automatic` are still set and **re-read** — before that, a setting that
     stuck and one that did not wrote the exact same log line, because the line
     was the *intended* list, which is the textbook false oracle. And the 20 h throttle no longer covers the services
     at all — only the Store: `Set-Service` is local and free, and the stamp
     being written only on complete success meant one service refusing to hold
     blocked it **forever**, so the four winget calls ran at every logon and
     daily, indefinitely.
  2. **The kiosk shell blocked ALL UWP activation — the real blocker.** With
     `Winlogon\Shell` pointing at `steam-shell.ps1`, *every* UWP app failed to
     activate: Settings, Windows Security, Store, Xbox, all `Class not
     registered`. The Microsoft-account sign-in form **is** a UWP app
     (`Microsoft.AAD.BrokerPlugin`), so Xbox Live sign-in died with
     **`0x80040154` = `REGDB_E_CLASSNOTREG`**, surfaced through the game as
     "We couldn't sign you in to Xbox Live", with no button to click. Stage 30
     now sets `Shell = explorer.exe`; `assets/desktop-chrome.ps1` (AtLogOn task)
     restores the bare look (wallpaper via registry, `HideIcons`, taskbar
     auto-hide) and `assets/steam-hold-notice.ps1` (AtLogOn task) keeps the
     STEAM.HOLD warning the old shell carried. Both tasks are independent of the
     shell — that is the point.
  **`HKCU\...\Run\Steam` MUST be removed, and stage 30 does it.** Under the
  kiosk that key was never executed (only Explorer processes `Run`) and slept
  harmlessly; with Explorer it relaunches Steam in duplicate, Apollo finds Steam
  already there, and `steam-session.ps1` — *whose exit closes the session* —
  returns at once: **"La connexion a ete interrompue" on every Moonlight
  attempt**, measured. Restoring the kiosk without restoring that key is the
  safe rollback; the reverse loses remote access.
  **Two execution traps paid on 2026-09-04, both measured on the guest, both
  test-guarded now.** (1) **`ExecutionTimeLimit` is a `System.String`, not a
  `TimeSpan`**: `(New-ScheduledTaskSettingsSet).ExecutionTimeLimit` is `PT72H`,
  and it is the *cmdlet parameter* that converts a `TimeSpan` to ISO-8601 — an
  assignment to the property deposits `00:05:00`, which
  `Register-ScheduledTask` rejects (`The task XML contains a value which is
  incorrectly formatted or out of range. (37,36):ExecutionTimeLimit:00:05:00`).
  Under `$ErrorActionPreference = 'Stop'` that killed stage 30, and with it the
  whole provisioning run. Write it as `New-ScheduledTaskSettingsSet
  -ExecutionTimeLimit …`, the way `40-agent.ps1` always did. (2)
  **`$ErrorActionPreference` is dynamically scoped, so a dot-sourced asset
  inherits the stage's `Stop`** — `gaming-services.ps1`'s "never throws"
  contract was false purely by being called from stage 34, where
  `& $winget … 2>&1` turned any native stderr line into a terminating error;
  and since the scheduled task runs under the default `Continue`, the two entry
  paths did **not** behave the same on failure, which is the opposite of what
  the file's header claims. It is restored **inside the function**, never at
  file level (that would disarm the `Stop` the rest of stage 34 relies on).
  Dead ends worth not repeating: `DISM /RestoreHealth` + `sfc /scannow` both
  succeed and report **no integrity violation** (nothing is corrupt);
  `FilterAdministratorToken=1` changes nothing (the built-in `-500` account is
  not the obstacle — AppX installs and activates fine under it); and **`winget`
  proves nothing about UWP health**, since it runs as a *Desktop AppX* (full
  trust), never in an AppContainer. To launch a UWP app with no shell, use
  `IApplicationActivationManager.ActivateApplication` (CLSID
  `45BA127D-10A8-46EA-8AB7-56EA9078943C`, `[PreserveSig]`) — but it *activates*
  without *displaying*: `ApplicationFrameHost` never starts without a shell, so
  no window is ever created.

* **`PROVISION_VERSION` must move whenever the SEQUENCE moves — B3 → B4 on
  2026-09-04, and the guest was measured at B1.** `guest-ready-watch.py`
  compares the marker's `provision_version` against `payload.PROVISION_VERSION`
  to decide whether a guest is current, so a constant that stays put while the
  sequence changes makes a guest provisioned *before* the change read as up to
  date. The winget chain added two stages, changed the session shell and three
  assets, and left the constant at `B3` — a console that had never seen winget
  would have passed for one that had. Measured the same day on the production
  guest: `C:\nivuus\state\PROVISION.done` says `provision_version=B1`,
  `completed=2026-08-26`, and `C:\nivuus\state` holds only the twelve
  `*.ps1.done` of that run — no `32-retro`, no `33-winget`, no
  `34-gaming-services`. **Everything committed here since 2026-08-26 exists on
  that console only because someone put it there by hand.** Written up as debt
  **C7** in `docs/console-dettes.md`, with the standing rule: *before
  concluding anything from an absence in a guest log, read
  `PROVISION.done` and compare it to `PROVISION_VERSION`* — a stage that never
  ran leaves no trace, and that absence reads exactly like a failure.
