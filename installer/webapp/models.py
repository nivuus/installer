"""Pydantic models for the Nivuus install configuration.

These validate the wizard payload before it is written to config.json and handed
to the install engine. Field names mirror the JSON keys the engine reads.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

# Feature keys the wizard offers; the engine gates each step on these.
KNOWN_FEATURES = {
    "os-base", "kvm-vfio", "thermal", "networking", "wifi-ap",
    "firewall", "docker", "home-assistant", "mqtt", "gpu-passthrough",
    "retro",
}


class DiskConfig(BaseModel):
    path: str = Field(..., description="Target disk device, e.g. /dev/nvme0n1")
    use_lvm: bool = False

    @field_validator("path")
    @classmethod
    def _abs_dev(cls, v: str) -> str:
        if not v.startswith("/dev/"):
            raise ValueError("disk path must be a /dev/ device")
        return v


class UserConfig(BaseModel):
    username: str = Field(..., min_length=1, max_length=32)
    password: Optional[str] = None
    password_hash: Optional[str] = None
    ssh_key: str = ""
    ssh_port: int = Field(22, ge=1, le=65535)
    password_auth: bool = True

    @field_validator("username")
    @classmethod
    def _valid_username(cls, v: str) -> str:
        if not v[0].isalpha() or not all(c.isalnum() or c in "_-" for c in v):
            raise ValueError("invalid Linux username")
        return v


class WanConfig(BaseModel):
    mode: str = "dhcp"  # "pppoe" | "dhcp"
    interface: str = ""
    vlan: int = Field(835, ge=1, le=4094)
    pppoe_user: str = ""
    pppoe_password: str = ""

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("pppoe", "dhcp"):
            raise ValueError("wan.mode must be 'pppoe' or 'dhcp'")
        return v


class WifiApConfig(BaseModel):
    enabled: bool = False
    country: str = "FR"
    private_ssid: str = "Nivuus"
    private_passphrase: str = ""
    public_ssid: str = ""
    public_passphrase: str = ""
    dual_band: bool = True
    interfaces_24: list[str] = Field(default_factory=list)
    interfaces_5: list[str] = Field(default_factory=list)

    @field_validator("private_passphrase", "public_passphrase")
    @classmethod
    def _wpa_len(cls, v: str) -> str:
        if v and not (8 <= len(v) <= 63):
            raise ValueError("WPA passphrase must be 8–63 characters")
        return v


class GpuPassthrough(BaseModel):
    enabled: bool = False
    ids: list[str] = Field(default_factory=list)


class CpuConfig(BaseModel):
    isolcpus: str = ""


class InstallConfig(BaseModel):
    disk: DiskConfig
    hostname: str = "nivuus"
    domain: str = ""
    user: UserConfig
    locale: str = "en_US.UTF-8"
    timezone: str = "Europe/Paris"
    wan: WanConfig = Field(default_factory=WanConfig)
    wifi_ap: WifiApConfig = Field(default_factory=WifiApConfig)
    gpu_passthrough: GpuPassthrough = Field(default_factory=GpuPassthrough)
    cpu: CpuConfig = Field(default_factory=CpuConfig)
    features: list[str] = Field(default_factory=lambda: ["os-base"])
    suite: str = "bookworm"
    mirror: str = "http://deb.debian.org/debian"

    @field_validator("hostname")
    @classmethod
    def _hostname(cls, v: str) -> str:
        if not v or len(v) > 63 or not all(c.isalnum() or c == "-" for c in v):
            raise ValueError("invalid hostname")
        return v

    @field_validator("features")
    @classmethod
    def _features(cls, v: list[str]) -> list[str]:
        unknown = set(v) - KNOWN_FEATURES
        if unknown:
            raise ValueError(f"unknown features: {sorted(unknown)}")
        # os-base is always required.
        if "os-base" not in v:
            v = ["os-base", *v]
        # retro (RetroArch, via the `retro` package) runs on the Windows
        # guest VM; checking it without also checking kvm-vfio (the VM
        # itself) cannot work. Refuse it here, at submit time, rather than
        # let it surface later as a failed step on a machine with no screen.
        if "retro" in v and "kvm-vfio" not in v:
            raise ValueError(
                "'retro' requires 'kvm-vfio' (the Windows guest VM)"
            )
        return v
