# Local package updater — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a machine running Nivuus packages can see which ones are behind their
latest GitHub release and lay the new release in place (`nivuus check`,
`nivuus update <name>`), replaying the package's own `install` then `activate`.

**Architecture:** four small modules next to the engine they reuse
(`installer/packages/`): `state.py` owns `/etc/nivuus/packages.json`,
`releases.py` talks to the GitHub API and verifies archives, `updater.py`
decides and lays, `nivuus_cli.py` is the operator surface. The engine's
parser, runner and topological sort are reused unchanged.

**Tech Stack:** Python 3.11+ stdlib only (`urllib`, `hashlib`, `tarfile` with
`filter="data"`, `fcntl`), PyYAML already required by the engine. No new
dependency: the download/verify step is ~60 lines of stdlib, while tools such
as `eget`/`dra` would add a binary to every target without knowing the
engine's hooks or state.

**Spec:** `docs/superpowers/specs/2026-09-08-releases-et-mise-a-jour-locale-design.md`
(sections *L'état sur la machine cible*, *L'updater*, *La CLI*, *Le timer*).

## Global Constraints

- English everywhere in code, messages included (policy / Coding rules).
- No source file over 500 lines.
- Every path overridable: `NIVUUS_PACKAGES_DIR`, `NIVUUS_STATE_FILE`,
  `NIVUUS_STAMP_DIR`, `NIVUUS_CACHE_DIR`.
- Fail closed: missing or mismatching SHA256 aborts before any write.
- A failed lay is loud and never retried automatically; state is written
  only on success, `failed` + `target_version` otherwise.
- A package without `source:` is never updated, and says so.
- State file stays 0600 (it holds wizard answers verbatim).

## Scope (decided 2026-09-28)

In: `source:` in the parser, state extension, updater, CLI
(`list/check/update/status/adopt`), check-only daily timer, engine deploys
the CLI and timer, `source:` added to the five package repos, `activate`
hook in home-stock that restarts Home Assistant.

Deferred, named: Home Assistant `update` entities through the mqtt agent,
`nivuus update --self`, the `shell` → `nivuus-shell` rename, `console`
declaring `source: {github: nivuus/installer, path: console}` (the parser
and updater support `path`; the manifest change is not made).

## Adoption — not in the spec, needed by this machine

The spec's end-to-end test assumes a state file the dev machine does not
have. `nivuus adopt <package-dir>` records a package laid by hand: it copies
the package's tracked files (`git archive HEAD` for a git work tree) to
`PACKAGES_DIR/<name>`, records `{"version": <manifest version>, "state":
"installed", "adopted_at": …}` and runs NO hook — it declares what is already
there. It records no `answers`: a package with wizard questions and no
recorded answers is refused by `update` with a message naming the gap,
rather than replayed with empty answers.

## Review Focus

1. A release whose manifest `name` differs from the package being updated →
   refused before replacement.
2. A release whose manifest `version` differs from its tag → refused.
3. A tarball entry escaping the extraction dir (`../`, absolute, symlink) →
   refused by `tarfile` `data` filter, nothing replaced.
4. A required package absent from state, or in `failed` → refused, named.
5. Two concurrent `nivuus update` → second one refuses at once, names the lock.

---

### Task 1: `source:` in the manifest parser

**Files:** Modify `installer/packages/manifest.py`; Test
`scripts/tests/test_packages_manifest.py`.

**Produces:** `Source(github: str, path: str = "")`, `Manifest.source:
Source | None`.

- [ ] Tests: valid `source: {github: nivuus/home-stock}`; with `path`;
      absent → `None`; non-mapping, missing `github`, `github` not
      `owner/repo`, unknown key, unsafe `path` → `ManifestError`.
- [ ] Implement `_parse_source`; run the suite; commit
      `feat(packages): declare where a package's releases come from`.

### Task 2: state file

**Files:** Create `installer/packages/state.py`; Modify
`installer/packages/activate_cli.py` (use `state.STATE_FILE`,
`state.STAMP_DIR`); Test `scripts/tests/test_packages_state.py`.

**Produces:** `STATE_FILE`, `STAMP_DIR`, `load() -> dict`,
`save(state: dict) -> None` (atomic, 0600), `status(record) -> str`
(`installed` when the key is absent), `mark_installed(state, name, version,
answers=None)`, `mark_failed(state, name, target_version, error)`,
`StateError`.

- [ ] Tests: missing file → `{}`; engine-written record reads `installed`;
      `mark_failed` keeps the installed `version`; `mark_installed` clears
      `target_version`/`error`, sets `updated_at`; save is 0600 and atomic;
      malformed JSON → `StateError`.
- [ ] Implement; commit `feat(packages): extend the package state file`.

### Task 3: releases

**Files:** Create `installer/packages/releases.py`; Test
`scripts/tests/test_packages_releases.py` (local HTTP server fixture).

**Produces:** `Release(repo, tag, version, archive_name, archive_url,
sums_url, archive_digest, notes)`, `latest_release(repo) -> Release`,
`download(release, cache_dir) -> str` (verified archive path),
`version_key(v) -> tuple[int, int, int]`, `ReleaseError`.
`GITHUB_API` overridable by `NIVUUS_GITHUB_API` (tests).

- [ ] Tests: tag `v1.2.0` → version `1.2.0`; non-semver tag refused;
      missing archive or `SHA256SUMS` asset refused; wrong sum refused and
      the file removed; API `digest` disagreeing with `SHA256SUMS` refused;
      archive absent from `SHA256SUMS` refused.
- [ ] Implement; commit `feat(packages): fetch and verify GitHub releases`.

### Task 4: updater

**Files:** Create `installer/packages/updater.py`; Test
`scripts/tests/test_packages_updater.py`.

**Consumes:** Tasks 1–3, `discovery.discover`, `dependencies.install_order`,
`runner.run_install/run_activate`, `common.hardware.detect_all`.

**Produces:** `check() -> list[Pending]` (writes
`STAMP_DIR/available.json`), `update(names, fetch=latest_release) ->
list[str]`, `adopt(package_dir) -> str`, `lock()` context manager,
`UpdateError`.

Lay order per package: download+verify → extract to
`PACKAGES_DIR/.<name>.staging` → load manifest (real parser) → name and
version match → wizard answers present when questions exist → requires
installed → swap directories → `run_install(root="/")` → remove stamp →
`run_activate` → write stamp → `mark_installed`. Any exception after the swap
→ `mark_failed`, re-raise.

- [ ] Tests with a fake `fetch` returning archives built in a temp dir and
      hooks that write marker files: up-to-date package untouched; update
      lays, runs install then activate, records version; rerun is a no-op;
      failing hook → `failed` + `target_version`, not retried by the next
      `update` without the name; the five Review Focus cases; adopt copies
      and records without hooks.
- [ ] Implement; commit `feat(packages): lay a new release of an installed package`.

### Task 5: CLI, timer, engine deployment

**Files:** Create `installer/packages/nivuus_cli.py`,
`configs/systemd/nivuus-check.service`, `configs/systemd/nivuus-check.timer`,
`installer/install-engine/steps/package_updates.py`; Modify
`installer/install-engine/steps/packages.py` (call it),
`installer/Makefile` (new suites); Test `scripts/tests/test_nivuus_cli.py`,
`scripts/tests/test_install_engine_package_updates.py`.

- [ ] CLI: `list`, `check`, `update [name…]`, `status [name]`,
      `adopt <dir>`; unknown first word → `exec nivuus-<word>` from `PATH`,
      else usage error 2.
- [ ] Engine: symlink `/usr/local/sbin/nivuus` →
      `/opt/nivuus/installer/packages/nivuus_cli.py`, copy both units,
      enable the timer in `timers.target.wants`.
- [ ] Commit `feat(packages): nivuus command and daily release check`.

### Task 6: package repositories

- [ ] `source: {github: nivuus/<name>}` in home-stock, home-manager,
      home-desk, desk, media-manager (`feat:` PR each, merged → release).
- [ ] home-stock: `hooks/activate.py` restarting the `homeassistant`
      container when it runs; manifest comment updated.

### Task 7: real run on this machine

- [ ] `nivuus adopt` home-manager and home-stock from their clones.
- [ ] `nivuus check` shows home-stock behind; `nivuus update home-stock`;
      state carries the new version; HA restarted; new services present.
- [ ] Rerun: no change, no error.
