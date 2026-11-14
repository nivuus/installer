# agent.exe — vendored binary, not built here

This is a **compiled** artefact of `nivuus/desk` (the Guacamole agent), not
source code owned by this repository. It cannot be rebuilt from this
repository, and **the `nivuus/desk` revision it was built from is not
recorded anywhere**: its provenance is unknown.

What is known about the tracked file (measured 2026-11-14):

| | |
|---|---|
| size | 20 468 152 bytes |
| sha256 | `63491776adc9879009470435c4fd97ba17a2a7e9bdd2d8ede2734883f9e21eea` |

History, from `git log`: a first build (10 786 304 bytes, sha256
`03f209e29e66c66fe002006c6553cd000745ed43cf1c63e738f89d86442bfb1b`) was
committed on 2026-08-28 (`dc4cf42`), extracted from the production Windows
VM's payload directory. It was replaced by the current file in `639c3cd`
(2026-08-29), a commit whose message is about something else, and reached
`main` with the squash `8f9f8be`. Nothing records which build that is.

**Pinned.** `console/guest/fetch_payload.py` carries `PACKAGED_AGENT_SHA256`,
and `install_packaged_agent()` refuses to copy a file that does not match it,
before anything lands in the offline payload tree; it then verifies the
copy's sha256 against the source. Replacing this binary therefore means
updating that constant and this table together, on purpose.

**Known cost, accepted for now.** This duplicates ownership of an artefact
that logically belongs to `nivuus/desk`. The intended end state is an agent
built by `nivuus/desk` CI, published as a release asset and fetched through
`pinned_installers.PINNED`, with this vendored copy deleted.
