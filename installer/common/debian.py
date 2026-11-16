"""The Debian suite a fresh install lays down when the wizard names none.

One value, read by the web portal's model and by the install engine, so the
two cannot drift apart. trixie (Debian 13) since 2026-11-16: the reference
host runs it, and Docker's repository serves it. bookworm stays selectable.
"""
DEFAULT_SUITE = "trixie"
