# Console activation: run-time defects and test-double lessons

* **Three run-time-only defects fixed 2026-08-28, task 4 of
  `2026-08-28-console-observabilite`** — none visible from reading either
  file alone, all found by tracing what the OTHER phase's own code does to
  this one's state:
  1. **The regime domain used to feed a silent, permanent replay loop.**
     Once `guest-ready-watch.py` redefines the domain without install media
     (see the `activate` bullet in [installer-console-package.md](installer-console-package.md)), `guest_steps.domain_defined()` used
     to look only for the two install-media ISO paths — absent on a regime
     domain — so it read "not done" and replayed `domain.py define`
     *without* `--keyed-varstore` (that flag is `guest-ready-watch.py`'s own
     escape hatch, never this step's), which hit `guard_fresh_varstore()`
     forever (the varstore the earlier media-carrying `define` already
     created never goes away) and refused with a remedy —
     `virsh undefine Windows --nvram` — that would have **reinstalled
     Windows over a console that already works**. `domain_defined()` now
     also checks the passthrough disk's own PCI address, read from the
     `<hostdev>`'s `<source>` element (`hostdev_source_addresses()`,
     measured 2026-08-28 against `virsh dumpxml Windows` on the real
     production domain: the source address is exactly
     `domain='0x0000' bus='0x03' slot='0x00' function='0x0'`, matching
     `lspci`'s `144d:a808` at `0000:03:00.0`, and libvirt never touches its
     attribute order the way it does the SIBLING guest-side `<address
     type='pci' .../>` a few lines below, which is not this identity and is
     deliberately never read as it) — and treats a media-less domain
     already carrying the right disk as a **terminal, legitimate** outcome.
  2. **The same predicate never noticed a changed `dedicated_nvme`.**
     `source_iso`/`iso_out` are FIXED paths under the guest workdir,
     unrelated to which physical disk was chosen — build reruns, `define`
     silently didn't. The PCI-address check above fixes both defects at
     once: media match alone is no longer sufficient, the disk must also
     agree.
  3. **`guest-ready-watch.py` logged "installation non demarree" every 2
     minutes forever, on a console that was simply resting** (most of a
     console's life, between sessions) — the timer never distinguished "just
     went not-running" from "still not running, same as last tick". It now
     logs the transition once, via a `not_started_reported` flag in its own
     persisted state, and stays silent until a genuine transition (running,
     then not-running again) clears it.
  Tests: `test_console_guest_steps.py` (regime-domain and changed-disk
  cases, plus `hostdev_source_addresses()`/`domain_matches_disk()` directly)
  and `test_console_guest_ready.py` (silence across repeated ticks, a
  resumed message after a genuine transition) — both proven to fail against
  the pre-fix code (`git stash` the two implementation files, rerun) before
  being proven to pass against it, `__pycache__` purged, `python -B`.

`hardware.py` is now split by that same principle: `installer/common/hardware.py`
detects **capabilities** (coarse: is there an IOMMU, a discrete GPU, a spare
NVMe — `list_gpus` no longer carries `ids`, `cpu_topology` no longer carries
`isolcpus`), and `console/hardware.py` detects the **details** in its resolve
phase.

**`domain.py` is REPAIRED, dated 2026-08-28 — measured, not inferred.**
`python3 domain.py xml`, run from `console/guest/`, now produces real domain
XML on this hardware (`main()` returns 0 and 5402 characters of XML — see
`test_windows_guest_production_domain.py`, which calls `main()` for real
instead of stubbing it out). The break this paragraph used to describe (an
`ImportError` at `main()`'s entry, because `HardwareError` had moved to
`console/hardware.py` without `domain.py` following) is fixed: `HardwareError`,
`passthrough_nvme()` and `pci_slot_functions()` live in `console/hardware.py`,
and `domain.py` reaches them with `sys.path.insert(0, str(HERE.parent))`
followed by `from hardware import HardwareError` (`domain.py:262-263`).

**The lesson that outlives the bug: that import is lazy on purpose — written
inside `main()`, not at module scope — and a lazy import lets `import
domain.py` succeed while calling `domain.main()` still fails.** That is
exactly why the break was invisible for a whole phase: every suite in the
aggregator imported the module (which worked) and called `domain_xml()`
directly with explicit keyword arguments (which never touches hardware
detection), so a green run said nothing about whether `main()` itself could
run. `test_windows_guest_production_domain.py` now closes that gap with one
assertion that actually calls `main()` end-to-end — the only one in the
suite that does. Keep the import lazy (the comment at `domain.py:259-261`
explains why: the test's own `sys.path` only carries `console/guest`, and a
module-scope `import hardware` would break every other test in the file) —
but never again let "imports cleanly" stand in for "runs cleanly" when a
lazy import is involved; a suite that only imports proves nothing about the
function that does the importing.

**A test double MORE PERMISSIVE than production hides the disagreement it
should reveal (2026-08-28, third occurrence on this project).** `build_run()`
started calling `runner(build_cmd, env=env)`; `guest_steps.default_runner`
grew the parameter, and so did the bench double (`FakeBuildRunner.__call__(self,
argv, *, env=None)`) — but `console/hooks/activate.py`'s `classifying_runner`,
**the only runner production ever uses**, did not. Measured: `TypeError`,
classified as a "panne" at the `build` step, so `activate` died at the third of
its five steps and neither the `TMPDIR` fix nor the qemu `chown` ever ran —
with all 33 suites green. Two rules came out of it. (1) `classifying_runner`
now forwards `**kwargs` blind: it adds nothing to the call, it only re-labels
the exception, so the next parameter cannot reproduce this. (2) The corpus now
drives the **production runner itself** through the `build` step
(`test_console_activate.py`, with a fake *interpreter* standing in for
`python`, never a fake runner) — that seam had no assertion at all, which is
exactly how the defect crossed it.

**A TEST DOUBLE THAT SATISFIES A PRECONDITION PRODUCTION CANNOT SATISFY MAKES
A SAFETY GUARD GREEN IN TEST AND INERT IN PRODUCTION (2026-09-05).** The worst
form of the previous entry, and the only one that can cost 363 GB.
`guest_steps.refuse_implicit_wipe()` exists to stop a `--disk-mode wipe`
reached *by omission* from recreating the whole partition table — on this host
one disk carries both `C:` and the games partition `D:` (Steam library, the
logged-in session, `shortcuts.vdf`, `D:\Emulation`, and
`D:\state\apollo\credentials`, the Apollo pairing root). It decided with
`domain_matches_disk()`, which resolves the disk through `/sys/block`. **The
console's disk is bound to vfio-pci — the point of the passthrough — so it has
no `/sys/block` entry at all**, `pci_address_for_device()` returns `None`,
the match reads `False`, and the guard returned silently. Measured on the real
host with a bench (real read-only `virsh`, real `/sys/block`, a runner raising
a sentinel before any subprocess): an activation with **no** `--disk-mode` was
**not** refused. Twenty-four suites were green throughout, the guard's own
among them, because every test injected a `pci_address_of` double returning the
address unconditionally — and the test's comment *described* the vfio problem
before neutralising it in the fixture, which reads on review as if the case had
been handled. The guard's own docstring named the trap and then depended on the
very lookup it said was impossible. **Three rules.** (1) When a guard's
predicate can answer "cannot tell", that is a THIRD outcome, never folded into
"no" — `disk_pci_identity()` now returns `None` distinctly, and only a disk
*proven* different lets a wipe through; doubt is the normal state here, not the
exception. (2) A guard reachable only from the `run()` side is bypassed by its
own step's `already_done()` shortcut — `refuse_implicit_wipe()` is called from
`build_done()` first, because a stamped ISO built from mode-less answers *is*
an erasing ISO and re-validates its own fingerprint. (3) **Every guard needs at
least one test driving the REAL resolver**, not only the double that answers
correctly; keep the double for the nominal case, never as the only path.

**AND A DEAD INFORMANT IS NOT AN ALIBI — settle the doubt with a fact, not a
policy (2026-09-05).** The same guard used to return silently whenever
`defined_xml()` answered `None`, reasoning that an unreachable libvirtd proves
nothing. True of the *daemon*, false of the *question*: a domain's persistent
definition is a **file**, `/etc/libvirt/qemu/<name>.xml`, present whether or not
anything is running to serve it (measured: `virsh uri` → `qemu:///system`, no
`LIBVIRT_DEFAULT_URI`, no `uri_default`, no session tree; `Windows.xml` there at
9188 B). So the choice was never binary between "refuse on a dead daemon" and
"don't block a fresh install": **daemon mute + definition on disk → refuse**
(a console is there, only the ability to read it is lost); **daemon mute + no
definition → pass** (plausibly fresh machine). Match the name **exactly** — libvirt
leaves `Windows.xml.backup-*` next to the real file, and a prefix test would
read a backup as a definition. Three limits are now written **in the docstring**,
which is what the previous version lacked: system scope only (a
`qemu:///session` host must inject its own reader); it proves EXISTENCE, never
IDENTITY (no daemon ⇒ no XML ⇒ it can refuse a disk the defined console never
used — one explicit answer against a lost games partition); and a console
installed then `undefine`d is invisible to it. Corollary for tests:
`definition_on_disk` is injected everywhere as "absent", or the suite would
conclude from whatever VMs exist on the machine running it — and would go red on
the reference host itself.

**A guard whose printed remedy is unreachable teaches people to bypass it
(2026-09-05).** The same refusal told operators to answer `--disk-mode rebuild
--target-disk-verified`, while `console/wizard.yaml` collected **neither** key
— so the only way out was hand-editing the answers file. Both questions now
exist; `disk_mode` is a **required `choix` with NO default**, deliberately: a
default is not a statement, and defaulting it to `wipe` would have made the
answer "explicit" for `_disk_mode()` and silenced the guard for everyone.

**A HIBERNATED CONSOLE IS `shut off` — `domain_up()` cannot tell it from a
machine that never booted (2026-08-28).** The whole energy strategy rests on S4
(`vm-idle-shutdown.sh` runs `shutdown /h /f`), and `virsh start` *resumes* that
session. So the `start` step's boot-key assist (12 `KEY_ENTER` past the LTSC
"Press any key to boot from CD or DVD......" prompt) was one `virsh domstate`
away from typing into a live desktop, and the activation unit re-runs at every
boot until the stamp exists. The discriminant that does hold is **what the
domain is wired to**: keys are sent only while it carries BOTH installation
media — the shape the `define` step produces and `redefine_steady_state()`
removes once the guest is provisioned.
