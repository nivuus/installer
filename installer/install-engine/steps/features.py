"""Step 8: apply the selected Nivuus features inside the chroot.

Each feature is self-contained and gated on the wizard's feature list.
networking/wifi/firewall render Jinja2 templates from the wizard answers;
docker/home-assistant deploy the application layer; thermal deploys the host
RAPL/fan policy. The VM setup that used to live here (KVM/VFIO packages, CPU
partitioning hooks, hugepages, kernel command line, retro) is gone - it is
now the console package's manifest and install hook (see console/).
"""
from __future__ import annotations

import os
import shutil
import uuid

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .util import StepError, chroot_run, write_file

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")

# Default bridge layout (generic; the wizard could override addresses later).
BRIDGES = {
    "localBridge": {"gateway": "192.168.0.1", "prefix": 24, "zone": "home"},
    "publicBridge": {"gateway": "192.168.2.1", "prefix": 24, "zone": "public"},
    "internalBridge": {"gateway": "192.168.3.1", "prefix": 24, "zone": "internal"},
}

NM_DIR = "etc/NetworkManager/system-connections"


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

    if "thermal" in features:
        _thermal(target, nivuus_dir, emit)
    if "networking" in features:
        _networking(config, target, env, emit)
    if "wifi-ap" in features:
        _wifi_ap(config, target, env, emit)
    if "firewall" in features:
        _firewall(config, target, emit)
    if "docker" in features:
        _docker(target, emit)
    if features & {"home-assistant", "mqtt"}:
        _home_assistant_mqtt(target, nivuus_dir, features, emit)


# --------------------------------------------------------------------------- #
def _thermal(target, nivuus_dir, emit) -> None:
    """Deploy the host thermal policy - all that survived install.sh.

    This is a HOST policy, not a VM one: RAPL power capping and the fan curve
    apply whether or not a guest ever runs. But its gaming/idle modes are
    driven by the console package's libvirt hooks, through
    `nivuus-cpu-mode@{gaming,idle}.service`. That unit name is therefore a
    PUBLIC CONTRACT of this repository: the package calls it if it exists and
    does nothing if it does not, which is what lets the package install on a
    Debian that has never seen this installer.
    """
    emit.info("features", 80, "Installing host thermal policy…")
    src = os.path.join(target, nivuus_dir.lstrip("/"),
                       "scripts/optimize-cpu-thermal.sh")
    if not os.path.isfile(src):
        emit.warn("features", 80,
                  f"optimize-cpu-thermal.sh absent de la charge utile ({src}) ; "
                  "politique thermique non installee")
        return
    dest = os.path.join(target, "usr/local/bin/optimize-cpu-thermal.sh")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)
    os.chmod(dest, 0o755)

    write_file(
        os.path.join(target, "etc/systemd/system/cpu-thermal-optimization.service"),
        "[Unit]\n"
        "Description=CPU thermal policy (RAPL caps, fan curve, core frequencies)\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/usr/local/bin/optimize-cpu-thermal.sh\n"
        "RemainAfterExit=yes\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n")
    chroot_run(target, ["systemctl", "enable",
                        "cpu-thermal-optimization.service"], check=False)


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
