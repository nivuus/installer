# Nivuus console package — test target.
#
# test          Run every suite under console/tests/ (Python and shell).

CONSOLE_DIR := $(CURDIR)

.PHONY: test help

help:
	@grep -E '^[a-zA-Z_-]+:.*' $(MAKEFILE_LIST) | sed 's/:.*//' | sort

# Run the console package's own test suite (no root, no chroot, no ISO).
#
# PYTHON is overridable because several of these suites import pydantic,
# which is NOT in the Debian base: `make test PYTHON=/path/to/venv/bin/python3`.
#
# 19 suites run here (2026-08-28): the 6 test_console_* suites,
# test_retro_marker_bridge and test_vm_wake_gate (the wake-on-demand gate
# and the units that arm it), and the 11 test_windows_guest_* suites. All
# moved here from scripts/tests/ so the package carries its own tests ahead
# of the git filter-repo --path console split (see installer/Makefile's
# test-packages comment for why that split needs this done first).
#
# test_handle_vm_start.sh also lives in tests/ (moved for the same reason)
# but is NOT invoked below: the old scripts/tests aggregator never ran it
# either (it is shell, not Python, and the loop only ever called .py files),
# so wiring it up here would be new behaviour, not a straight move. It stays
# runnable by hand: `bash tests/test_handle_vm_start.sh`.
PYTHON ?= python3

test:
	@for t in test_console_hardware test_console_resolve test_console_install \
	          test_console_host_files test_console_wake_units test_console_activate \
	          test_vm_wake_gate \
	          test_retro_marker_bridge \
	          test_windows_guest_apollo test_windows_guest_autounattend \
	          test_windows_guest_build test_windows_guest_domain \
	          test_windows_guest_fetch_payload test_windows_guest_iso \
	          test_windows_guest_media test_windows_guest_payload \
	          test_windows_guest_production_domain test_windows_guest_provision \
	          test_windows_guest_retro_sync; do \
	    echo "--- $$t"; \
	    $(PYTHON) $(CONSOLE_DIR)/tests/$$t.py || exit 1; \
	done
