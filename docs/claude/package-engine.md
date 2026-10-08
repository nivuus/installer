# Package engine (nivuus.dev/v1)

**Package engine (2026-08-27)**: `installer/packages/` implements the
`nivuus.dev/v1` contract — a declarative `nivuus-package.yaml` plus three hooks
(`resolve`/`install`/`activate`). Packages are sibling repositories embedded
into the ISO via `PACKAGE_REPOS=… make build-iso` and discovered at
`/opt/nivuus-packages/*/` (override with `NIVUUS_PACKAGES_DIR`).

Three properties carry the design and are easy to break by accident:
* **`resolve` is read-only and runs before `partition`.** That is the only
  reason `bootloader` can stay where it is in `run.py`: the kernel cmdline is
  known before the disk is touched. Moving a package's cmdline contribution
  later would force the whole pipeline to be reordered.
* **`iommu` is read from the ACPI tables (`DMAR`/`IVRS`), never from
  `/sys/kernel/iommu_groups`.** The live ISO boots *without* `intel_iommu=on` —
  adding it is exactly what a passthrough package asks for — so a check on the
  active state would answer "no" on every capable machine and no such package
  would ever be offered.
* **The engine detects capabilities, the package detects details.** The engine
  must answer `requires.capabilities` before running any hook, so it cannot
  delegate. It stays coarse (`iommu`, `gpu-discrete`, `nvme-dedicated`,
  `cpu-hybrid`); the precise work — PCI functions, IOMMU groups, `vfio-pci.ids`
  — belongs to `resolve`.
* **A fourth event carries what `resolve` measured across the reboot
  (`installer/packages/facts.py`, 2026-08-28).** `{"event":"facts","facts":{…}}`
  — `resolve` **returns** facts, the engine persists them into
  `etc/nivuus/packages.json` (same 0600 file as the answers, mode unchanged),
  and `activate_cli.py` merges them into the `hw` it hands the activate hook.
  It exists because a `platform` package routinely measures something the
  install itself destroys — the console's dedicated NVMe is bound to
  `vfio-pci` by the very cmdline this installer writes, so at first boot
  `/sys/block` no longer lists it. **Precedence is settled and enforced in
  code: the fresh snapshot wins, a fact only fills a key detection did not
  produce** — a fact describes the world *before* the reboot, so it may never
  mask a measurement that can still be taken; a package wanting the
  pre-reboot value of something still observable must name its fact
  distinctly (`dedicated_nvme_size_bytes`, not `size_bytes`). A dropped fact
  is warned about, never silent. Facts are **not** gated on tier: unlike
  `kernel-cmdline`/`modules`/`hugepages-mib` they reach no boot chain, only
  the activate phase of the package that produced them. The channel has three
  links (runner → state file → activate_cli) and each has its own named
  assertion in the suites: cutting one names it.

Two more findings from implementing the engine, both worth knowing before
touching it:
* **`resolve` being read-only is a CONVENTION, not a sandbox.** Tested
  directly: a `resolve` hook that writes to disk succeeds, and the write
  persists — nothing chroots or restricts the subprocess. What the pipeline
  ordering actually depends on is that the engine never *asks* `resolve` to
  write and never *uses* anything it wrote, not that `resolve` is incapable. A
  malicious package owns the machine from the `install` phase onward
  regardless, since that runs as root. The rule protects against accident and
  ordering mistakes — real, worth having — but must be described as that, not
  as a security boundary. Real sandboxing would be its own project.
* **`chroot_base.py:9` imports `crypt`, removed from the Python stdlib in 3.13
  (PEP 594).** Used for exactly one thing (line 98): hashing the user
  account's password. The live ISO builds on **bookworm**
  (`iso-build/auto/config --distribution bookworm`), Python 3.11, so
  production is unaffected today — but `run.py` cannot even be imported on a
  Python 3.13 host, and the day the ISO base moves to trixie the installer
  breaks at the step that creates the user account, at the very end of an
  otherwise successful install. Remedies: `passlib`, `openssl passwd -6`, or
  `chpasswd -e` inside the chroot. Dated technical debt, not a bug to fix now
  — out of the package-engine plan's scope.

Activation uses a stamp (`/var/lib/nivuus/packages/<name>.activated`), not a
self-disabling unit: an interrupted activation must retry at the next boot
rather than believe it succeeded.

**Local updater (2026-09-28, plan `2026-09-28-updater-local`).** `nivuus
list|check|update [name…]|status|adopt <dir>` (`installer/packages/nivuus_cli.py`,
symlinked to `/usr/local/sbin/nivuus` by `steps/package_updates.py`, which
also enables the check-only `nivuus-check.timer`). A package is followed only
if its manifest declares `source: {github: owner/repo[, path: sub]}`; the tag
`vX.Y.Z` is the version, the archive is verified against `SHA256SUMS` **and**
the API asset `digest` (fail closed), extracted by `packages/archive.py` (member-by-member `realpath` checks —
**not** `tarfile`'s `filter=`, which bookworm's Python 3.11.2 rejects with
`TypeError`; the suites run on 3.13 and never saw it, a PR review did),
its manifest must carry the same name and version, recorded answers must
satisfy the new questions, `requires.packages` must be installed and not
`failed` — all before the directory is swapped. Then `install` (root `/`) +
`activate` replay, and the state gets `state`/`target_version`/`error`/
`updated_at`. A failed lay is never retried by a bare `nivuus update`, only by
naming it. `adopt` records a hand-laid package (tracked files only, no hook,
no invented answers); `nivuus answers <name> key=value…` then records them,
typed and validated by the package's own wizard rules, required secrets
asked on the terminal (never on argv); `secret=` with no value asks a recorded one again, the only way to correct it. An adopted package never ran `resolve`, so it also lacks the facts `activate` may need (the console's NVMe size and PCI address are unreadable once vfio-pci owns the disk); `nivuus facts <name> key=<json>…` (`packages/fact_record.py`) records them, each value decoded as JSON and the whole set validated by the engine's own `parse_facts_event`, under `state[name]["facts"]` — the key the updater and `activate_cli` already read, so a hand-recorded fact and a resolved one are the same thing to `activate`. `nivuus update --self` lays the
release's `installer/` subtree over the directory the CLI runs from (swap
with restore, refuses a git checkout, never combined with packages), records
the version in `STAMP_DIR/installer.json` — **not** in the package state,
which every command iterates as packages — then refreshes, from the same
verified archive, the installer's own units (`nivuus-check.*`,
`nivuus-package-activate@`) **that the machine already has**
(`packages/units.py`: content compared, atomic 0644, then `Reload` and a
restart of a changed enabled timer over D-Bus via `busctl`, never
`systemctl`, which a PID-namespaced session cannot use). It does so even when
the code is already current, so a machine laid by an older updater catches up. `console` declares `source: {github: nivuus/installer, path: console}`
(2026-10-03): the updater follows it through installer's releases, whose
archive carries `console/` stamped with installer's version. **Deferred**: HA
`update` entities via mqtt, the `shell` rename.

**Arming `activate` takes three copies onto the target, and the whole phase is
dead if any is missed** (it was, on the first cut of this branch, while the
install still reported success — `systemctl enable` ran with `check=False`).
`apply_packages()` does all three: it copies
`configs/systemd/nivuus-package-activate@.service` out of the payload into
`{target}/etc/systemd/system/`, chmods `activate_cli.py` executable **in
place** at `/opt/nivuus/installer/packages/` — the unit's `ExecStart` points
there, *not* at `/usr/local/sbin`, because the script computes its own
`sys.path` from `__file__` and a copy elsewhere cannot import `common`/`packages` —
and copies each **selected** package's directory to
`{target}/opt/nivuus-packages/<name>/` (the live medium's copy is on the LIVE
root and does not survive the reboot). Enablement is a **direct symlink** into
`multi-user.target.wants/`, which is precisely what `systemctl enable` does for
a `WantedBy=multi-user.target` template unit — chosen over shelling into the
chroot because `systemctl` fails silently in constrained environments (see the
PID-namespace note in [host-shell-gotchas.md](host-shell-gotchas.md)) and a symlink either exists or raises.

**A fourth broken link, found by re-review after the first three were fixed:
`python3` itself was never guaranteed on the target.** `debootstrap.py`'s
`BASE_INCLUDE` has no `python3`, and the only other installer of it
(`install.sh`'s `python3-pip`) runs only behind the optional `kvm-vfio`/
`thermal` features — so a minimal install that selects a package but neither
feature reached first boot with no interpreter for the unit's own
`ExecStart`, silently, forever (the apt call was `check=False`, and the
stamp file is only written on success, so it retried every boot with no
error surfaced anywhere). Fixed by splitting the one apt call into two:
`ACTIVATE_APT = ["python3", "python3-yaml"]` (the activate phase's own hard
requirements — `activate_cli.py` needs an interpreter to run at all and
re-parses the manifest with PyYAML at first boot, and nothing else pulls
either in) is installed in its own `apt-get` call whose failure raises
`StepError` and stops the install; the packages' own declared `apt`
(`manifest.apt`) stays a separate, deliberately lenient `check=False` call
— a package may still be usable without an optional dependency, so only a
warning is emitted. The two must never be merged back into one call: that
is exactly the "armed, advertised, and silently inert" failure class this
whole area exists to prevent.

**Kernel parameters are validated in `plan_packages`, not in `bootloader`.**
The allowlist lives in `bootloader.py::grub_defaults`, which runs at step 7 —
after `partition` has wiped the disk. `plan_packages` now calls it purely for
its validation, so `vfio-pci.ids=$(x)` is refused while the target is still
untouched. Same reason the package state file (`etc/nivuus/packages.json`) is
written **after each package**, not once at the end: a residue that describes
itself beats a partially applied install with no record.

**The wizard does not offer packages yet (2026-08-27, a named gap, not a footnote).**
`webapp/static/js/app.js` and `webapp/templates/wizard.html` call no
`/api/packages` route — packages exist in the engine and in `discover()`, but
nothing renders them for a human to pick. The only way to install one today is
a config carrying `packages: {"console": {...}}` directly (the engine path,
exercised by `test_install_engine_packages.py` and the end-to-end proof in the
console-package-hote plan's Task 7), never through the portal. Wiring the
wizard is deliberately deferred, not forgotten.

Tests: `cd installer && make test-packages` (needs a Python with `pydantic`
and `jinja2` — not the Debian base — via `PYTHON=/path/to/venv/bin/python`).
**39 suites, measured 2026-08-29 (after the `requires.packages` guards, the
three host-script suites and `test_windows_guest_winrm_exec` were wired into
the aggregator): 38 exit 0, and
`test_webapp_models` could not be run on a python3.13-only base, where the
installed `pydantic` belongs to a python3.11 that no longer has a binary.**
13 run directly by `installer/Makefile` (the engine/webapp/packages suites that
stay outside `console/`), 3 shell suites delegated to its `test-scripts`
prerequisite (`test_hw_blackbox`, `test_net_rps_ecores`,
`test_pcie_wifi_link_guard` — they guard hardware-facing scripts but build fake
trees under `mktemp`, so they need no hardware and no Python; they are a
prerequisite rather than a trailing call so a base without `pydantic` cannot
mask them), and 23 delegated to `console/Makefile`'s own `test` target —
the 8 `test_console_*` suites (`test_console_guest_steps` and
`test_console_guest_ready` are phase 2d's own), `test_vm_wake_gate`,
`test_retro_marker_bridge`, the 12 `test_windows_guest_*` suites and the
shell suite `test_handle_vm_start.sh`, all of them living under
`console/tests/`. That last one was wired up on 2026-08-28 (it has its own
loop in `console/Makefile`, since the Python loop only runs `.py` files): it
is the only guard on the 2026-08-24 infinite-wake bug, and reintroducing
that bug was measured to leave every Python suite green. No file under
`console/` **source** imports from `installer/common` or anywhere else in
`installer/` — verified with `grep -rn 'from common\|import common' console/`,
which returns nothing. **That grep is too narrow to prove the whole
boundary, and does not**: `console/tests/test_console_resolve.py` puts
`installer/` on `sys.path` and imports `packages.manifest`/`packages.runner`/
`packages.wizard` to exercise `resolve` through the real engine contract —
a real, dated blocker for the day this package is extracted with
`git filter-repo`, not a source-code exception.
Spec: `docs/superpowers/specs/2026-08-27-decoupage-installer-console-design.md`,
phase 2d's own design: `docs/superpowers/specs/2026-08-28-console-activate-invite-design.md`.
