#!/usr/bin/env python3
"""retro_sync.py never waits on the guest for ever, and leaves no hold behind.

2026-11-16 audit:
  * N4 - the WinRM call had no time bound. A command frozen in the guest
    (Steam's -shutdown stuck on a modal dialog, retro.exe blocked on a share)
    blocked retro_sync for ever, while HoldKeeper kept refreshing the Steam
    hold every 60 s: Steam could not restart until someone killed the process.
  * N6 - HoldKeeper.stop() did not wait for its thread, so a touch_hold in
    flight could write the hold again after remove_hold had deleted it.

Nothing here reaches Windows: the WinRM client is a fake script, the guest a
recording double.

Run: python3 console/tests/test_retro_sync_bounds.py
"""
import os
import pathlib
import sys
import tempfile
import threading
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "console" / "guest"))

import retro_sync  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def raised(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


# --- N4: the client process is bounded and killed ------------------------- #

with tempfile.TemporaryDirectory() as d:
    pid_file = pathlib.Path(d) / "pid"
    fake = pathlib.Path(d) / "winrm_exec.py"
    fake.write_text("import os, time\n"
                    f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
                    "time.sleep(60)\n")
    real_exec = retro_sync.WINRM_EXEC
    retro_sync.WINRM_EXEC = fake
    try:
        started = time.monotonic()
        error = raised(retro_sync._winrm_run, "ps", "Start-Sleep 3600", timeout=1)
        elapsed = time.monotonic() - started
    finally:
        retro_sync.WINRM_EXEC = real_exec
    check("a WinRM call that does not return raises GuestTimeout",
          type(error).__name__, "GuestTimeout")
    check("... which is a GuestError, so the caller's handling applies",
          isinstance(error, retro_sync.GuestError), True)
    check("... after its time bound, not after the command", elapsed < 10, True)
    check("... and names the bound", "1 s" in str(error), True)
    pid = int(pid_file.read_text()) if pid_file.exists() else None
    alive = True
    if pid is not None:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
    check("... and the client process is gone", (pid is not None, alive), (True, False))


# --- N4: every Guest call carries a bound, sized per step ----------------- #

class Transport:
    """The run() seam of the real Guest: records each call and its bound."""

    def __init__(self, hang_on=None):
        self.calls = []
        self.hang_on = hang_on

    def __call__(self, kind, script, guest_ip=None, timeout=None):
        self.calls.append((script, timeout))
        if "taskkill" in script:
            return 0, "", ""
        if self.hang_on and self.hang_on in script:
            raise retro_sync.GuestTimeout(f"no answer within {timeout} s")
        return 0, "EXIT=0\n", ""


t = Transport()
g = retro_sync.Guest(run=t)
g.exists(r"C:\x")
check("a plain guest call is bounded by WINRM_TIMEOUT_S",
      t.calls[-1][1], retro_sync.WINRM_TIMEOUT_S)
for sub in ("identite", "install", "scan", "sync"):
    g.retro([sub])
    check(f"`retro {sub}` is bounded by its own step budget",
          t.calls[-1][1], retro_sync.RETRO_TIMEOUTS_S.get(sub, retro_sync.WINRM_TIMEOUT_S))
check("every call carries a finite bound",
      all(isinstance(b, (int, float)) and b > 0 for _, b in t.calls), True)
check("the sync budget is the longest (artwork is fetched game by game)",
      retro_sync.RETRO_TIMEOUTS_S["sync"] >= max(retro_sync.RETRO_TIMEOUTS_S.values()), True)

# A retro step that times out is stopped in the guest before the error is
# raised: otherwise retro.exe could keep writing shortcuts.vdf after the hold
# is gone and Steam is back.
t = Transport(hang_on="retro.exe")
g = retro_sync.Guest(run=t)
error = raised(g.retro, ["sync"])
check("a timed-out retro step raises GuestTimeout", type(error).__name__, "GuestTimeout")
kills = [s for s, _ in t.calls if "taskkill" in s]
check("... after asking the guest to stop the retro process tree", len(kills), 1)
check("... by its image name, with its children",
      bool(kills) and "/T" in kills[0] and "retro.exe" in kills[0], True)
check("... and says it was stopped", "stopped" in str(error), True)


# --- N6: stop() waits for a touch in flight ------------------------------- #

entered, release, touches = threading.Event(), threading.Event(), []


def slow_touch(_guest):
    entered.set()
    release.wait(5)
    touches.append(time.monotonic())


keeper = retro_sync.HoldKeeper(object(), interval=0.01, toucher=slow_touch)
keeper.start()
check("a touch is in flight", entered.wait(5), True)
stopped_at = []
stopper = threading.Thread(target=lambda: (keeper.stop(), stopped_at.append(time.monotonic())))
stopper.start()
stopper.join(0.3)
check("stop() does not return while a touch is in flight", stopper.is_alive(), True)
release.set()
stopper.join(5)
check("stop() returns once the touch is done", stopper.is_alive(), False)
check("no touch ends after stop() returned (remove_hold comes after it)",
      bool(stopped_at) and all(t <= stopped_at[0] for t in touches), True)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - every guest call is bounded and the hold keeper stops before the hold is removed")
