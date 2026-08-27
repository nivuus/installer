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

.PHONY: build-iso test-engine test-portal test-vm windows-unattend test-packages clean help

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
# PYTHON is overridable because three of these suites import pydantic, which is
# NOT in the Debian base: `make test-packages PYTHON=/path/to/venv/bin/python3`.
# The CI runs no Python tests at all (ci.yml sets test-paths: ""), so this target
# is the only aggregator there is - and an aggregator that fails for a missing
# dependency reads as "the engine is broken", which is the wrong thing to learn.
#
# 26 suites (2026-08-27): the original 9, the 6 the console-package phase
# created or touched, and all 11 test_windows_guest_* suites, which run clean
# standalone (no root, no network, no mounted Windows medium) and were simply
# never wired into any aggregator before. NOTE: a green run here is not proof
# that installer/windows-guest/domain.py works - see CLAUDE.md, Installer
# Architecture, for the coverage gap both domain suites share.
PYTHON ?= python3

test-packages:
	@for t in test_packages_manifest test_packages_capabilities \
	          test_packages_discovery test_packages_conflicts \
	          test_packages_wizard test_packages_runner \
	          test_install_engine_bootloader test_install_engine_packages \
	          test_webapp_models \
	          test_common_hardware test_console_hardware test_console_resolve \
	          test_console_install test_install_engine_features \
	          test_retro_marker_bridge \
	          test_windows_guest_apollo test_windows_guest_autounattend \
	          test_windows_guest_build test_windows_guest_domain \
	          test_windows_guest_fetch_payload test_windows_guest_iso \
	          test_windows_guest_media test_windows_guest_payload \
	          test_windows_guest_production_domain test_windows_guest_provision \
	          test_windows_guest_retro_sync; do \
	    echo "--- $$t"; \
	    $(PYTHON) $(dir $(INSTALLER_DIR))scripts/tests/$$t.py || exit 1; \
	done

# Build the secondary ISO that unattends the Windows guest install (root:
# inspecting the LTSC medium needs a loop mount).
# Usage: sudo make windows-unattend WINDOWS_ISO=/path/ltsc.iso DRIVERS_DIR=/path/payload
windows-unattend:
	@test -n "$(WINDOWS_ISO)" || { echo "Set WINDOWS_ISO=/path/to/ltsc.iso"; exit 1; }
	@test -n "$(DRIVERS_DIR)" || { echo "Set DRIVERS_DIR=/path/to/payload"; exit 1; }
	python3 $(INSTALLER_DIR)/windows-guest/build.py \
	  --windows-iso "$(WINDOWS_ISO)" --drivers-dir "$(DRIVERS_DIR)" \
	  $(if $(OUTPUT),--output "$(OUTPUT)",)

clean:
	-cd $(ISO_DIR) && sudo lb clean --purge
	-rm -rf $(ISO_DIR)/config/includes.chroot/opt/nivuus-installer
	-rm -rf $(ISO_DIR)/config/includes.chroot/opt/nivuus-src
	-rm -f $(TARGET_IMG)
