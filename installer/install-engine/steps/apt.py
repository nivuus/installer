"""apt-get install inside the target, with failures named after their owner.

Every component the engine installs is installed in its own apt-get call and
a failure is fatal. Two things go wrong otherwise, and both have shipped:

  * check=False on a required component: the install reports success and the
    machine boots without Docker, sshd or the MQTT agent;
  * one shared call for several owners: apt refuses the whole transaction for
    a single unknown name, so one package's typo silently took every other
    package's dependencies down with it.

The StepError names the owner (a feature or a package), the names it asked
for, and the last lines apt printed, which is where apt says which name it
could not locate. apt output carries package names and mirror URLs, never a
wizard answer, so it can go to the progress stream.
"""
from __future__ import annotations

from .util import StepError, chroot_run

# apt's own diagnosis ("E: Unable to locate package x") is on its last lines.
OUTPUT_TAIL_LINES = 8


def _tail(proc) -> str:
    text = "\n".join(part.strip() for part in
                     (getattr(proc, "stdout", "") or "",
                      getattr(proc, "stderr", "") or "") if part.strip())
    return "\n".join(text.splitlines()[-OUTPUT_TAIL_LINES:])


def install_failure(target: str, owner: str, names: list[str]) -> str | None:
    """Install `names` for `owner`; return the failure description, or None.

    For callers that install for several owners and must try every one of
    them before failing (see packages.apply_packages).
    """
    proc = chroot_run(target, ["apt-get", "install", "-y", *names], check=False)
    if proc.returncode == 0:
        return None
    message = (f"{owner}: apt-get install failed (exit {proc.returncode}) "
               f"for: {' '.join(names)}")
    tail = _tail(proc)
    return f"{message}\n{tail}" if tail else message


def install(target: str, owner: str, names: list[str]) -> None:
    """Install `names` for `owner`, or raise StepError naming both."""
    failure = install_failure(target, owner, names)
    if failure:
        raise StepError(failure)
