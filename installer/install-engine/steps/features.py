"""Step 8: apply the selected Nivuus features inside the chroot.

Each feature is self-contained and gated on the wizard's feature list. The KVM/
VFIO/thermal feature reuses the repo's install.sh (run with computed, non-
hardcoded kernel params); networking/wifi/firewall render Jinja2 templates from
the wizard answers; docker/home-assistant deploy the application layer. retro
(RetroArch, via the `retro` package) is the odd one out: it installs nothing
in the chroot - it runs entirely on the Windows guest VM, provisioned
separately and later by windows-guest/build.py - so its only job here is to
record the operator's choice durably on the target; see _retro().
"""
from __future__ import annotations

import json
import os
import uuid

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .util import StepError, chroot_run, chroot_stream, write_file

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")

# Default bridge layout (generic; the wizard could override addresses later).
BRIDGES = {
    "localBridge": {"gateway": "192.168.0.1", "prefix": 24, "zone": "home"},
    "publicBridge": {"gateway": "192.168.2.1", "prefix": 24, "zone": "public"},
    "internalBridge": {"gateway": "192.168.3.1", "prefix": 24, "zone": "internal"},
}

NM_DIR = "etc/NetworkManager/system-connections"

# Where the retro toggle is recorded on the target (see _retro()).
RETRO_STATE_PATH = "etc/nivuus/retro.json"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(enabled_extensions=()),
        keep_trailing_newline=True,
    )


def _render(env: Environment, template_name: str, **ctx) -> str:
    return env.get_template(template_name).render(**ctx)


def apply_features(config: dict, target: str, nivuus_dir: str, hw: dict,
                   emit) -> None:
    features = set(config.get("features", []))
    env = _env()

    if features & {"kvm-vfio", "thermal"}:
        _kvm_vfio_thermal(config, target, nivuus_dir, hw, features, emit)
    if "networking" in features:
        _networking(config, target, env, emit)
    if "wifi-ap" in features:
        _wifi_ap(config, target, env, emit)
    if "firewall" in features:
        _firewall(config, target, emit)
    if "docker" in features:
        _docker(target, emit)
    if "retro" in features:
        _retro(target, features, emit)
    if features & {"home-assistant", "mqtt"}:
        _home_assistant_mqtt(target, nivuus_dir, features, emit)


# --------------------------------------------------------------------------- #
def _kvm_vfio_thermal(config, target, nivuus_dir, hw, features, emit) -> None:
    emit.info("features", 80, "Installing KVM/VFIO + thermal optimisation…")

    # Generic kernel params from detected hardware.
    cpu = hw.get("cpu", {})
    isolcpus = (config.get("cpu", {}).get("isolcpus")
                or cpu.get("isolcpus") or "")
    gpu_cfg = config.get("gpu_passthrough", {})
    vfio_ids = ",".join(gpu_cfg.get("ids", [])) if gpu_cfg.get("enabled") else ""

    env_extra = {
        "NIVUUS_DIR": nivuus_dir,
        "NIVUUS_ASSUME_YES": "1",
        "NIVUUS_IN_CHROOT": "1",
    }
    if isolcpus:
        env_extra["NIVUUS_ISOLCPUS"] = isolcpus
    if vfio_ids:
        env_extra["NIVUUS_VFIO_IDS"] = vfio_ids

    code = chroot_stream(
        target, ["bash", f"{nivuus_dir}/install.sh", "--non-interactive"],
        on_line=lambda l: emit.info("features", 82, l[:120]),
        extra_env=env_extra,
    )
    if code != 0:
        raise StepError("install.sh failed inside the chroot")


# --------------------------------------------------------------------------- #
def _networking(config, target, env, emit) -> None:
    emit.info("features", 85, "Configuring NetworkManager bridges and WAN…")
    nm_path = os.path.join(target, NM_DIR)
    os.makedirs(nm_path, exist_ok=True)

    for name, spec in BRIDGES.items():
        content = _render(env, "bridge.nmconnection.j2", name=name,
                          uuid=str(uuid.uuid4()), gateway=spec["gateway"],
                          prefix=spec["prefix"], zone=spec["zone"])
        write_file(os.path.join(nm_path, f"bridge-{name}.nmconnection"),
                   content, mode=0o600)

    # WAN: PPPoE over VLAN, or plain DHCP.
    wan = config.get("wan", {})
    mode = wan.get("mode", "dhcp")
    if mode == "pppoe":
        parent = wan.get("interface", "")
        vlan_id = int(wan.get("vlan", 835))
        if not parent:
            raise StepError("PPPoE selected but no WAN interface specified")
        write_file(
            os.path.join(nm_path, "wan-vlan.nmconnection"),
            _render(env, "wan-vlan.nmconnection.j2", uuid=str(uuid.uuid4()),
                    parent=parent, vlan_id=vlan_id), mode=0o600)
        write_file(
            os.path.join(nm_path, "pppoe-wan.nmconnection"),
            _render(env, "pppoe-wan.nmconnection.j2", uuid=str(uuid.uuid4()),
                    parent=parent, vlan_id=vlan_id,
                    username=wan.get("pppoe_user", ""),
                    password=wan.get("pppoe_password", "")), mode=0o600)


# --------------------------------------------------------------------------- #
def _wifi_ap(config, target, env, emit) -> None:
    wifi = config.get("wifi_ap", {})
    if not wifi.get("enabled"):
        return
    emit.info("features", 88, "Configuring hostapd WiFi access point…")
    chroot_run(target, ["apt-get", "install", "-y", "hostapd"], check=False)
    chroot_run(target, ["systemctl", "unmask", "hostapd"], check=False)

    hostapd_dir = os.path.join(target, "etc/hostapd")
    os.makedirs(hostapd_dir, exist_ok=True)
    country = wifi.get("country", "FR")
    common = dict(
        country=country,
        private_ssid=wifi.get("private_ssid", "Nivuus"),
        private_passphrase=wifi.get("private_passphrase", ""),
        public_ssid=wifi.get("public_ssid", ""),
        public_passphrase=wifi.get("public_passphrase", ""),
        public_bridge="publicBridge",
    )
    ifaces24 = wifi.get("interfaces_24", [])
    if ifaces24:
        write_file(
            os.path.join(hostapd_dir, "2.4Ghz.conf"),
            _render(env, "hostapd.conf.j2", band="2.4GHz", hw_mode="g",
                    channel=6, interface=ifaces24[0], bridge="localBridge",
                    public_iface=(ifaces24[1] if len(ifaces24) > 1 else ""),
                    **common), mode=0o600)
    ifaces5 = wifi.get("interfaces_5", [])
    if ifaces5 and wifi.get("dual_band", True):
        write_file(
            os.path.join(hostapd_dir, "5Ghz.conf"),
            _render(env, "hostapd.conf.j2", band="5GHz", hw_mode="a",
                    channel=36, interface=ifaces5[0], bridge="localBridge",
                    public_iface=(ifaces5[1] if len(ifaces5) > 1 else ""),
                    **common), mode=0o600)


# --------------------------------------------------------------------------- #
def _firewall(config, target, emit) -> None:
    emit.info("features", 90, "Installing firewall (firewalld + fail2ban)…")
    chroot_run(target, ["apt-get", "install", "-y", "firewalld", "fail2ban",
                        "nftables"], check=False)
    chroot_run(target, ["systemctl", "enable", "firewalld"], check=False)
    chroot_run(target, ["systemctl", "enable", "fail2ban"], check=False)
    # Persist IP forwarding for routing between WAN and bridges.
    write_file(os.path.join(target, "etc/sysctl.d/99-nivuus-forward.conf"),
               "net.ipv4.ip_forward = 1\n")


# --------------------------------------------------------------------------- #
def _docker(target, emit) -> None:
    emit.info("features", 92, "Installing Docker engine…")
    # docker.io from Debian repos avoids needing an external apt key at install.
    chroot_run(target, ["apt-get", "install", "-y", "docker.io",
                        "docker-compose-v2"], check=False)
    chroot_run(target, ["systemctl", "enable", "docker"], check=False)


# --------------------------------------------------------------------------- #
def _retro(target, features, emit) -> None:
    """Record that retrogaming was requested, on the target filesystem.

    Retro (RetroArch, via the `retro` package) runs entirely on the Windows
    guest VM, built separately and later by windows-guest/build.py -
    possibly on this very host, but as a manual step the wizard cannot see
    or trigger. build.py falls back to reading this file (RETRO_STATE_PATH)
    when its own --retro flag is not given explicitly, so this marker is
    the only durable trace of the operator's choice once the installer has
    moved on.

    Called only when "retro" was selected (see apply_features), like every
    other feature here: an unchecked install writes nothing, exactly as it
    did before this option existed. That still agrees with build.py, which
    treats an absent marker as "off" - the unchecked case needs no marker
    of its own to say so.

    retro depends on the Windows guest VM (the "kvm-vfio" feature); the
    wizard already refuses that combination at submit time (see
    webapp/models.py). By the time a config reaches this step, though,
    partitioning, the base system and the bootloader are already done -
    raising here would fail an otherwise-complete install over a file
    nothing reads yet. Warn and record retro as disabled instead: the
    wizard's own guard is what actually protects the common case.
    """
    if "kvm-vfio" not in features:
        emit.warn(
            "features", 93,
            "'retro' was selected without 'kvm-vfio' (the Windows guest VM "
            "it depends on); recording retrogaming as disabled rather than "
            "failing an otherwise-complete install")
        enabled = False
    else:
        emit.info("features", 93, "Retrogaming (Windows guest VM): enabled")
        enabled = True
    write_file(
        os.path.join(target, RETRO_STATE_PATH),
        json.dumps({"enabled": enabled}, indent=2) + "\n")


# --------------------------------------------------------------------------- #
def _home_assistant_mqtt(target, nivuus_dir, features, emit) -> None:
    emit.info("features", 95, "Deploying Home Assistant / MQTT layer…")
    # The MQTT agent lives in its own repository (nivuus/mqtt) and ships a .deb
    # built upstream by the ISO build when BUILD_MQTT_DEB=1. If present in the
    # payload, install it; otherwise skip - the source is NOT in this payload.
    deb_dir = os.path.join(target, nivuus_dir.lstrip("/"), "mqtt")
    if os.path.isdir(deb_dir):
        for fname in os.listdir(deb_dir):
            if fname.endswith(".deb"):
                emit.info("features", 96, f"Installing MQTT agent {fname}…")
                chroot_run(target, ["apt-get", "install", "-y",
                                    f"{nivuus_dir}/mqtt/{fname}"], check=False)
                break
    # HA + Mosquitto run as containers; deployment compose lives in the payload.
    # The actual `docker compose up` happens on first boot once networking is up.
