#!/usr/bin/env python3
"""Tests for the thermal feature - all that remains of install.sh.

install.sh had seven blocks; five were VM setup and now live in the console
package's install hook. What was left is fifteen lines: deploy the thermal
script and its unit. The NIVUUS_DIR / NIVUUS_IN_CHROOT / NIVUUS_ISOLCPUS /
NIVUUS_VFIO_IDS plumbing existed only to make that script runnable inside a
chroot, and it went with it.

Run: python3 scripts/tests/test_install_engine_features.py
"""
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "installer" / "install-engine"))
sys.path.insert(0, str(REPO / "installer"))

from steps import features  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


class FakeEmit:
    def __init__(self):
        self.lines = []

    def info(self, step, pct, msg):
        self.lines.append(msg)

    def warn(self, step, pct, msg):
        self.lines.append(msg)

    def error(self, step, pct, msg):
        self.lines.append(msg)


calls = []


def fake_chroot_run(target, cmd, **kwargs):
    calls.append(cmd)

    class R:
        returncode = 0
    return R()


features.chroot_run = features.apt.chroot_run = fake_chroot_run

# Sans la feature thermal, rien ne doit etre pose.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    features.apply_features({"features": ["os-base"]}, str(target),
                            "/opt/nivuus", {}, FakeEmit())
    check("aucune unite thermique sans la feature",
          (target / "etc/systemd/system/cpu-thermal-optimization.service").exists(),
          False)

# Avec la feature, l unite et le script arrivent.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    payload = target / "opt/nivuus/scripts"
    payload.mkdir(parents=True)
    (payload / "optimize-cpu-thermal.sh").write_text("#!/bin/bash\ntrue\n")

    features.apply_features({"features": ["os-base", "thermal"]}, str(target),
                            "/opt/nivuus", {}, FakeEmit())
    unit = target / "etc/systemd/system/cpu-thermal-optimization.service"
    check("l unite thermique est posee", unit.is_file(), True)
    check("elle pointe vers le script deploye",
          "/usr/local/bin/optimize-cpu-thermal.sh" in unit.read_text(), True)
    check("le script est deploye",
          (target / "usr/local/bin/optimize-cpu-thermal.sh").is_file(), True)
    check("l unite est activee",
          any("cpu-thermal-optimization.service" in " ".join(c) for c in calls),
          True)

# The firewall feature binds the guest VPN tunnel to the permanent external
# zone, on the chroot's files only: no reload, no firewall-cmd.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    vendor = target / "usr/lib/firewalld/zones"
    vendor.mkdir(parents=True)
    (vendor / "external.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<zone>\n  <short>External</short>\n</zone>\n')
    (vendor / "public.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<zone>\n  <short>Public</short>\n</zone>\n')
    calls.clear()
    features.apply_features({"features": ["os-base", "firewall"]}, str(target),
                            "/opt/nivuus", {}, FakeEmit())
    external = target / "etc/firewalld/zones/external.xml"
    check("the external zone lists the guest VPN tunnel",
          external.is_file() and external.read_text().count('<interface name="nvguest0"/>'), 1)
    check("no firewall command reaches the chroot",
          [c for c in calls if any(w in " ".join(c) for w in ("firewall-cmd", "reload", "nmcli"))],
          [])

# A firewalld that did not install is named as such, not as a missing zone.
with tempfile.TemporaryDirectory() as tmp:
    try:
        features.apply_features({"features": ["os-base", "firewall"]}, tmp,
                                "/opt/nivuus", {}, FakeEmit())
        failures.append("missing firewalld: no StepError")
    except features.StepError as exc:
        check("missing firewalld is reported as such",
              "firewalld did not install" in str(exc), True)

# --- required components fail the install when they fail to install ------- #
def failing_on(needle, code=100, stderr="E: Unable to locate package x"):
    """A fake chroot_run where the call containing `needle` fails.

    Mirrors util.chroot_run: raises StepError when check is on (the default),
    returns the failed process when the caller asked for check=False.
    """
    def fake(target, cmd, **kwargs):
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        if needle in " ".join(cmd):
            R.returncode = code
            R.stderr = stderr
            if kwargs.get("check", True):
                raise features.StepError(f"command failed ({code}): {' '.join(cmd)}")
        return R()
    return fake


def install_fails(label, config, fake, needle, target_setup=None):
    real = features.chroot_run
    features.chroot_run = features.apt.chroot_run = fake
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if target_setup:
                target_setup(pathlib.Path(tmp))
            try:
                features.apply_features(config, tmp, "/opt/nivuus", {}, FakeEmit())
                failures.append(f"{label}: no StepError")
            except features.StepError as exc:
                check(f"{label}: the error names {needle!r}", needle in str(exc), True)
    finally:
        features.chroot_run = features.apt.chroot_run = real


# Docker: Debian's names. docker-compose-v2 exists in no Debian suite, and
# asking apt for it made it refuse docker.io as well.
with tempfile.TemporaryDirectory() as tmp:
    calls.clear()
    features.apply_features({"features": ["os-base", "docker"]}, tmp,
                            "/opt/nivuus", {}, FakeEmit())
    check("docker installs Debian's engine and Compose plugin",
          [c for c in calls if c[:2] == ["apt-get", "install"]],
          [["apt-get", "install", "-y", "docker.io", "docker-compose"]])
    check("the Compose plugin is checked in the target",
          ["docker", "compose", "version"] in calls, True)
    check("docker is enabled", ["systemctl", "enable", "docker"] in calls, True)

install_fails("an unknown docker package fails the install",
              {"features": ["os-base", "docker"]},
              failing_on("docker.io", stderr="E: Unable to locate package docker-compose"),
              "Unable to locate package docker-compose")
install_fails("the docker failure names its feature",
              {"features": ["os-base", "docker"]}, failing_on("docker.io"),
              "feature docker")
install_fails("a suite without the Compose v2 plugin fails the install",
              {"features": ["os-base", "docker"]},
              failing_on("docker compose version", code=1), "Compose v2 plugin")
install_fails("docker that cannot be enabled fails the install",
              {"features": ["os-base", "docker"]},
              failing_on("enable docker", code=1), "enable docker")
install_fails("hostapd that does not install fails the install",
              {"features": ["os-base", "wifi-ap"], "wifi_ap": {"enabled": True}},
              failing_on("hostapd"), "feature wifi-ap")
install_fails("fail2ban that does not install fails the install",
              {"features": ["os-base", "firewall"]}, failing_on("fail2ban"),
              "feature firewall")
install_fails("fail2ban that cannot be enabled fails the install",
              {"features": ["os-base", "firewall"]},
              failing_on("enable fail2ban", code=1), "enable fail2ban")


def seed_agent(target):
    mqtt = target / "opt/nivuus/mqtt"
    mqtt.mkdir(parents=True)
    (mqtt / "mqtt-system-agent_1.0.0_amd64.deb").write_bytes(b"")


install_fails("an MQTT agent that does not install fails the install",
              {"features": ["os-base", "mqtt"]}, failing_on(".deb"),
              "MQTT agent", target_setup=seed_agent)

# --- SSH is closed on the WAN zones, kept on the LAN ones -------------------- #
VENDOR = {
    "external": "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<zone>\n"
                "  <short>External</short>\n  <service name=\"ssh\"/>\n"
                "  <masquerade/>\n</zone>\n",
    "public": "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<zone>\n"
              "  <short>Public</short>\n  <service name=\"ssh\"/>\n"
              "  <service name=\"dhcpv6-client\"/>\n</zone>\n",
    "home": "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<zone>\n"
            "  <short>Home</short>\n  <service name=\"ssh\"/>\n</zone>\n",
    "internal": "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<zone>\n"
                "  <short>Internal</short>\n  <service name=\"ssh\"/>\n</zone>\n",
}


def zone_text(target, zone):
    """The zone as firewalld will read it: /etc's copy, else the vendor's."""
    path = target / "etc/firewalld/zones" / f"{zone}.xml"
    if not path.is_file():
        path = target / "usr/lib/firewalld/zones" / f"{zone}.xml"
    return path.read_text()


def zone_has_ssh(target, zone):
    return '<service name="ssh"/>' in zone_text(target, zone)


with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    vendor = target / "usr/lib/firewalld/zones"
    vendor.mkdir(parents=True)
    for zone, text in VENDOR.items():
        (vendor / f"{zone}.xml").write_text(text)
    features.apply_features({"features": ["os-base", "firewall"]}, str(target),
                            "/opt/nivuus", {}, FakeEmit())
    check("ssh closed in external (the PPPoE WAN zone)",
          zone_has_ssh(target, "external"), False)
    check("ssh closed in public (DefaultZone: a DHCP WAN, the guest bridge)",
          zone_has_ssh(target, "public"), False)
    check("ssh kept in home (the LAN bridge)", zone_has_ssh(target, "home"), True)
    check("ssh kept in internal", zone_has_ssh(target, "internal"), True)
    check("the rest of the public zone is untouched",
          '<service name="dhcpv6-client"/>' in zone_text(target, "public"), True)

# The DHCP WAN's zone is firewalld's DefaultZone, read from the target.
with tempfile.TemporaryDirectory() as tmp:
    target = pathlib.Path(tmp)
    vendor = target / "usr/lib/firewalld/zones"
    vendor.mkdir(parents=True)
    for zone, text in VENDOR.items():
        (vendor / f"{zone}.xml").write_text(text)
    conf = target / "etc/firewalld/firewalld.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("DefaultZone=internal\n")
    features.apply_features({"features": ["os-base", "firewall"]}, str(target),
                            "/opt/nivuus", {}, FakeEmit())
    check("ssh closed in the configured DefaultZone",
          zone_has_ssh(target, "internal"), False)
    check("public is not assumed when it is not the DefaultZone",
          zone_has_ssh(target, "public"), True)

# Les features VM ont quitte ce fichier.
check("plus de _kvm_vfio_thermal", hasattr(features, "_kvm_vfio_thermal"), False)
check("plus de _retro", hasattr(features, "_retro"), False)

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK - all thermal feature tests passed")
