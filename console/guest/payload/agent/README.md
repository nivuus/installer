# agent.exe — vendored binary, not built here

This is a **compiled** artefact of `nivuus/desk` (the Guacamole agent), not
source code owned by this repository. No checkout of `nivuus/desk` exists on
this machine, so it cannot be rebuilt here — it was extracted from the
production Windows VM's payload directory
(`/media/data/nivuus-win-payload/agent/agent.exe`) and committed directly so
that a fresh machine can install this console without ever having run that
VM (see `console/README.md` for the full reserve behind this decision).

Measured at the time it was vendored (2026-08-28):

| | |
|---|---|
| size | 10 786 304 bytes |
| sha256 | `03f209e29e66c66fe002006c6553cd000745ed43cf1c63e738f89d86442bfb1b` |

**Known cost, accepted deliberately.** This duplicates ownership of an
artefact that logically belongs to `nivuus/desk`, and it will **drift** on
every new agent build since nothing here updates it automatically. Bumping
this file means re-extracting the new `agent.exe` from wherever `nivuus/desk`
produces it next, measuring its size/sha256 again, and updating both this
table and the commit message — there is no automated sync, and none is
planned by this package.

Consumed by `console/guest/fetch_payload.py` (`install_packaged_agent`),
which copies it into the offline payload tree at build time and verifies the
copy's sha256 against this file before the build can proceed.
