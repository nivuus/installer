# Nivuus installer — build and test targets.
#
# build-iso     Build the bootable installer ISO (root; needs live-build).
# test-engine   Run the install engine against a loopback disk image (root).
# test-portal   Launch the web portal locally on :8080 for wizard testing.
# test-vm       Boot the built ISO in QEMU (UEFI) with the portal on :8080.
# clean         Remove build artefacts.

INSTALLER_DIR := $(CURDIR)
ISO_DIR       := $(INSTALLER_DIR)/iso-build
ISO           := $(firstword $(wildcard $(ISO_DIR)/*.iso))
TARGET_IMG    ?= /tmp/nivuus-target.img
TARGET_SIZE   ?= 16G
OVMF          ?= /usr/share/OVMF/OVMF_CODE.fd

.PHONY: build-iso test-engine test-portal test-vm windows-unattend clean help

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
