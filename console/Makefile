# Nivuus console package — test target.
#
# test          Run every suite under console/tests/ (Python and shell).

CONSOLE_DIR := $(CURDIR)

.PHONY: test help

help:
	@grep -E '^[a-zA-Z_-]+:.*' $(MAKEFILE_LIST) | sed 's/:.*//' | sort

# Run the console package's own test suite (no root, no chroot, no ISO).
#
# PYTHON is overridable because 9 of these suites import jinja2 (through
# guest/domain.py, guest/autounattend.py, guest/apollo.py and
# guest/testdomain.py), which is NOT in the Debian base: `make test
# PYTHON=/path/to/venv/bin/python3`. Measured suite by suite, 2026-08-28, by
# blocking the jinja2 import: test_retro_marker_bridge and the 8 non-media
# test_windows_guest_* suites fail without it. NOT pydantic - nothing under
# console/ imports it; the single pydantic-dependent suite (test_webapp_models)
# stayed in installer/Makefile with the portal.
#
# 23 suites run here (2026-08-29): the 8 test_console_* suites (the 8th,
# test_console_guest_ready, guards the four-way installing/failed/ready
# classification that makes activate's Windows build observable after it
# hands back control), test_retro_marker_bridge and test_vm_wake_gate (the
# wake-on-demand gate and the units that arm it), the 12 test_windows_guest_*
# suites, and the shell suite test_handle_vm_start. All moved here from
# scripts/tests/ so the package carries its own tests ahead of the git
# filter-repo --path console split (see installer/Makefile's test-packages
# comment for why that split needs this done first).
#
# test_handle_vm_start.sh is shell, not Python, so it gets its own loop below
# rather than a `.py` entry. The old scripts/tests aggregator never ran it -
# wiring it up IS new behaviour, and deliberate: it is the only guard on the
# 2026-08-24 infinite-wake bug (an unreachable libvirtd read as a transitional
# state), and reintroducing that bug was measured to leave every Python suite
# green.
PYTHON ?= python3

test:
	@for t in test_console_hardware test_console_resolve test_console_install \
	          test_console_host_files test_console_wake_units test_console_activate \
	          test_console_guest_steps test_console_guest_ready \
	          test_vm_wake_gate \
	          test_retro_marker_bridge \
	          test_windows_guest_apollo test_windows_guest_autounattend \
	          test_windows_guest_build test_windows_guest_domain \
	          test_windows_guest_fetch_payload test_windows_guest_iso \
	          test_windows_guest_media test_windows_guest_payload \
	          test_windows_guest_production_domain test_windows_guest_provision \
	          test_windows_guest_retro_sync test_windows_guest_winrm_exec; do \
	    echo "--- $$t"; \
	    $(PYTHON) $(CONSOLE_DIR)/tests/$$t.py || exit 1; \
	done
	@for t in test_handle_vm_start; do \
	    echo "--- $$t"; \
	    bash $(CONSOLE_DIR)/tests/$$t.sh || exit 1; \
	done
