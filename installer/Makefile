# Nivuus installer — build and test targets.
#
# build-iso     Build the bootable installer ISO (root; needs live-build).
# test-engine   Run the install engine against a loopback disk image (root).
# test-portal   Launch the web portal locally on :8080 for wizard testing.
# test-vm       Boot the built ISO in QEMU (UEFI) with the portal on :8080.
# windows-unattend  Build the secondary ISO that unattends the Windows guest install.
# clean         Remove build artefacts.

INSTALLER_DIR := $(CURDIR)
ISO_DIR       := $(INSTALLER_DIR)/iso-build
ISO           := $(firstword $(wildcard $(ISO_DIR)/*.iso))
TARGET_IMG    ?= /tmp/nivuus-target.img
TARGET_SIZE   ?= 16G
OVMF          ?= /usr/share/OVMF/OVMF_CODE.fd

.PHONY: build-iso test-engine test-portal test-vm windows-unattend test-packages test-scripts clean help

help:
	@grep -E '^[a-zA-Z_-]+:.*' $(MAKEFILE_LIST) | sed 's/:.*//' | sort

build-iso:
	sudo $(ISO_DIR)/build.sh

# Standalone engine run against a fresh loopback image (fast iteration, no ISO).
# Usage: sudo make test-engine CONFIG=/path/to/config.json
test-engine:
	@test -n "$(CONFIG)" || { echo "Set CONFIG=/path/to/config.json"; exit 1; }
	@echo "Creating $(TARGET_SIZE) loopback image at $(TARGET_IMG)"
	truncate -s $(TARGET_SIZE) $(TARGET_IMG)
	$(eval LOOP := $(shell sudo losetup --find --show $(TARGET_IMG)))
	@echo "Loop device: $(LOOP)"
	NIVUUS_PROGRESS_DIR=/tmp/nivuus-progress \
	  sudo python3 $(INSTALLER_DIR)/install-engine/run.py \
	  --config $(CONFIG) --target /tmp/nivuus-mnt \
	  --nivuus-src $(dir $(INSTALLER_DIR)) ; \
	  sudo losetup -d $(LOOP)

test-portal:
	NIVUUS_PORTAL_HOST=0.0.0.0 NIVUUS_PORTAL_PORT=8080 \
	NIVUUS_PROGRESS_DIR=/tmp/nivuus-progress \
	  python3 $(INSTALLER_DIR)/webapp/main.py

test-vm:
	@test -n "$(ISO)" || { echo "No ISO found; run 'make build-iso' first"; exit 1; }
	@test -f $(TARGET_IMG) || truncate -s $(TARGET_SIZE) $(TARGET_IMG)
	qemu-system-x86_64 -enable-kvm -m 8192 -smp 8 \
	  -drive if=pflash,format=raw,readonly=on,file=$(OVMF) \
	  -drive file=$(ISO),media=cdrom \
	  -drive file=$(TARGET_IMG),format=raw,if=virtio \
	  -netdev user,id=n0,hostfwd=tcp::8080-:80 \
	  -device virtio-net,netdev=n0

# Run the package-engine test suite (no root, no chroot, no ISO).
#
# PYTHON is overridable because two of the 13 Python suites in the loop below
# need a dependency that is NOT in the Debian base: test_webapp_models needs
# pydantic v2 (bookworm ships v1) and test_install_engine_features needs
# jinja2 (the 3 shell suites of test-scripts need neither). Measured
# suite by suite, 2026-08-28. `make test-packages PYTHON=/path/to/venv/bin/python3`.
# The CI runs no Python tests at all (ci.yml sets test-paths: ""), so this target
# is the only aggregator there is - and an aggregator that fails for a missing
# dependency reads as "the engine is broken", which is the wrong thing to learn.
#
# 38 suites (2026-08-29, measured): 13 run directly below (the engine/webapp/
# packages suites that stay outside console/), 3 shell suites are delegated to
# the `test-scripts` prerequisite below, and 22 are delegated to
# `console/Makefile`'s own `test` target - every suite testing code that lives
# under console/ moved to console/tests/ so that a later `git filter-repo
# --path console` is mechanical (everything the package owns is already under
# it). That includes the shell suite test_handle_vm_start.sh, wired up on the
# console side (this loop only ever ran .py files). PYTHON is propagated to the
# sub-make because 9 console suites import jinja2 - not pydantic, which nothing
# under console/ uses. console/guest/domain.py is exercised for real by
# test_windows_guest_production_domain.py, which calls main() end-to-end rather
# than stubbing it out - see CLAUDE.md, Installer Architecture, "domain.py is
# REPAIRED", for how that suite closed the gap.
PYTHON ?= python3

test-packages: test-scripts
	@for t in test_packages_manifest test_packages_capabilities \
	          test_packages_discovery test_packages_conflicts \
	          test_packages_dependencies \
	          test_packages_wizard test_packages_runner \
	          test_install_engine_bootloader test_install_engine_packages \
	          test_packages_plan_order \
	          test_webapp_models \
	          test_common_hardware \
	          test_install_engine_features; do \
	    echo "--- $$t"; \
	    $(PYTHON) $(dir $(INSTALLER_DIR))scripts/tests/$$t.py || exit 1; \
	done
	@$(MAKE) -C $(dir $(INSTALLER_DIR))console test PYTHON=$(PYTHON)

# Run the host-maintenance script suites (no root, no hardware, no Python).
#
# hw-blackbox.py, net-rps-ecores.sh and pcie-wifi-link-guard.sh are not the
# package engine, so they do not belong in the test-packages loop - but until
# 2026-08-29 they were in NO make target at all, named only in CLAUDE.md as
# commands to type by hand. That is the blind spot the dependency guards were
# in: a suite no target runs is a suite the CI cannot run either, and this
# repo's CI runs no Python tests at all (ci.yml sets test-paths: "").
#
# They guard hardware-facing scripts but need no hardware: all three build a
# fake tree under mktemp. Measured 2026-08-29 on a machine with none of the
# devices they describe - 26 + 20 + 16 assertions, exit 0 each.
#
# They are a PREREQUISITE of test-packages rather than a trailing call, so that
# a base without pydantic - which stops the Python loop at test_webapp_models -
# cannot mask them. They need nothing, so they run first and always.
# Shell, so their own loop, as console/Makefile does for test_handle_vm_start.
test-scripts:
	@for t in test_hw_blackbox test_net_rps_ecores test_pcie_wifi_link_guard; do \
	    echo "--- $$t"; \
	    bash $(dir $(INSTALLER_DIR))scripts/tests/$$t.sh || exit 1; \
	done

# Build the secondary ISO that unattends the Windows guest install (root:
# inspecting the LTSC medium needs a loop mount).
# Usage: sudo make windows-unattend WINDOWS_ISO=/path/ltsc.iso DRIVERS_DIR=/path/payload
windows-unattend:
	@test -n "$(WINDOWS_ISO)" || { echo "Set WINDOWS_ISO=/path/to/ltsc.iso"; exit 1; }
	@test -n "$(DRIVERS_DIR)" || { echo "Set DRIVERS_DIR=/path/to/payload"; exit 1; }
	python3 $(dir $(INSTALLER_DIR))console/guest/build.py \
	  --windows-iso "$(WINDOWS_ISO)" --drivers-dir "$(DRIVERS_DIR)" \
	  $(if $(OUTPUT),--output "$(OUTPUT)",)

clean:
	-cd $(ISO_DIR) && sudo lb clean --purge
	-rm -rf $(ISO_DIR)/config/includes.chroot/opt/nivuus-installer
	-rm -rf $(ISO_DIR)/config/includes.chroot/opt/nivuus-src
	-rm -f $(TARGET_IMG)
