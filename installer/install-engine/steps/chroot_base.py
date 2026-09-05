"""Step 6: base configuration inside the chroot.

Locale, timezone, hostname/hosts, apt sources, the primary user account
(password + sudo + SSH key), and sshd hardening from the wizard answers.
"""
from __future__ import annotations

import os
import crypt

from .util import StepError, chroot_run, chroot_stream, write_file

# Core packages installed via apt in the chroot (NOT via debootstrap --include),
# so their dbus/logind/polkit dependency chains configure in the right order.
CORE_PACKAGES = [
    "network-manager", "openssh-server", "sudo", "dbus", "polkitd",
    "ifupdown", "iproute2", "iputils-ping",
]


def configure_base(config: dict, target: str, emit) -> None:
    _apt_sources(config, target)
    _install_core_packages(target, emit)
    _locale_and_time(config, target, emit)
    _hostname(config, target)
    _user(config, target, emit)
    _sshd(config, target, emit)


def _install_core_packages(target: str, emit) -> None:
    emit.info("base", 61, "Installing core packages (NetworkManager, SSH, dbus)…")
    chroot_run(target, ["apt-get", "update"])
    code = chroot_stream(
        target,
        ["apt-get", "install", "-y", "--no-install-recommends", *CORE_PACKAGES],
        on_line=lambda line: emit.info("base", 61, line[:120]),
    )
    if code != 0:
        raise StepError("failed to install core packages in the chroot")


def _apt_sources(config: dict, target: str) -> None:
    suite = config.get("suite", "bookworm")
    mirror = config.get("mirror", "http://deb.debian.org/debian")
    sources = "\n".join([
        f"deb {mirror} {suite} main contrib non-free non-free-firmware",
        f"deb {mirror} {suite}-updates main contrib non-free non-free-firmware",
        f"deb http://security.debian.org/debian-security {suite}-security "
        "main contrib non-free non-free-firmware",
    ]) + "\n"
    write_file(os.path.join(target, "etc/apt/sources.list"), sources)


def _locale_and_time(config: dict, target: str, emit) -> None:
    locale = config.get("locale", "en_US.UTF-8")
    timezone = config.get("timezone", "Europe/Paris")
    emit.info("base", 62, f"Configuring locale {locale} and timezone {timezone}…")

    write_file(os.path.join(target, "etc/locale.gen"), f"{locale} UTF-8\n")
    write_file(os.path.join(target, "etc/default/locale"), f"LANG={locale}\n")
    chroot_run(target, ["locale-gen"], check=False)

    # Timezone via symlink + /etc/timezone.
    localtime = os.path.join(target, "etc/localtime")
    if os.path.lexists(localtime):
        os.remove(localtime)
    os.symlink(f"/usr/share/zoneinfo/{timezone}", localtime)
    write_file(os.path.join(target, "etc/timezone"), f"{timezone}\n")


def _hostname(config: dict, target: str) -> None:
    hostname = config.get("hostname", "nivuus")
    domain = config.get("domain", "").strip()
    fqdn = f"{hostname}.{domain}" if domain else hostname
    write_file(os.path.join(target, "etc/hostname"), f"{hostname}\n")
    hosts = (
        "127.0.0.1\tlocalhost\n"
        f"127.0.1.1\t{fqdn} {hostname}\n"
        "::1\tlocalhost ip6-localhost ip6-loopback\n"
        "ff02::1\tip6-allnodes\nff02::2\tip6-allrouters\n"
    )
    write_file(os.path.join(target, "etc/hosts"), hosts)


def _user(config: dict, target: str, emit) -> None:
    user = config.get("user", {})
    username = user.get("username")
    if not username:
        raise StepError("config.user.username is required")
    emit.info("base", 66, f"Creating user account '{username}'…")

    chroot_run(target, ["useradd", "-m", "-s", "/bin/bash",
                        "-G", "sudo", username], check=False)

    # Password: prefer a pre-computed hash; otherwise hash the plaintext here.
    pw_hash = user.get("password_hash")
    if not pw_hash and user.get("password"):
        pw_hash = crypt.crypt(user["password"], crypt.mksalt(crypt.METHOD_SHA512))
    if pw_hash:
        chroot_run(target, ["usermod", "-p", pw_hash, username])
    # Lock root login by default (sudo via the user instead).
    chroot_run(target, ["passwd", "-l", "root"], check=False)

    ssh_key = (user.get("ssh_key") or "").strip()
    if ssh_key:
        ssh_dir = os.path.join(target, "home", username, ".ssh")
        os.makedirs(ssh_dir, exist_ok=True)
        write_file(os.path.join(ssh_dir, "authorized_keys"), ssh_key + "\n",
                   mode=0o600)
        # chown to the user's uid:gid (resolved inside the chroot).
        chroot_run(target, ["chown", "-R", f"{username}:{username}",
                            f"/home/{username}/.ssh"], check=False)
        os.chmod(ssh_dir, 0o700)


def _sshd(config: dict, target: str, emit) -> None:
    user = config.get("user", {})
    port = int(user.get("ssh_port", 22))
    password_auth = bool(user.get("password_auth", True))
    emit.info("base", 68, f"Configuring SSH (port {port})…")

    conf = (
        "# Managed by the Nivuus installer\n"
        f"Port {port}\n"
        "PermitRootLogin no\n"
        f"PasswordAuthentication {'yes' if password_auth else 'no'}\n"
        "PubkeyAuthentication yes\n"
        "X11Forwarding no\n"
    )
    write_file(os.path.join(target, "etc/ssh/sshd_config.d/10-nivuus.conf"), conf)
    chroot_run(target, ["systemctl", "enable", "ssh"], check=False)
    chroot_run(target, ["systemctl", "enable", "NetworkManager"], check=False)
