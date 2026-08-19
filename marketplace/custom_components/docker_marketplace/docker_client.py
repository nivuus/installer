"""Docker client wrapper for Docker Marketplace."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import docker
from docker.errors import APIError, ImageNotFound, NotFound
from docker.models.containers import Container

from .const import (
    DEFAULT_DOCKER_HOST,
    STATE_ERROR,
    STATE_NOT_INSTALLED,
    STATE_RUNNING,
    STATE_STOPPED,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ContainerStats:
    """Container statistics."""

    cpu_percent: float
    memory_usage: int
    memory_limit: int
    memory_percent: float
    network_rx: int
    network_tx: int


@dataclass
class ContainerInfo:
    """Container information."""

    id: str
    name: str
    image: str
    status: str
    state: str
    ports: dict[str, Any]
    created: str
    labels: dict[str, str]


class DockerClient:
    """Wrapper around Docker SDK."""

    def __init__(self, docker_host: str = DEFAULT_DOCKER_HOST) -> None:
        """Initialize Docker client."""
        self._docker_host = docker_host
        self._client: docker.DockerClient | None = None

    def _create_client(self) -> docker.DockerClient:
        """Create Docker client (blocking, run in thread)."""
        return docker.DockerClient(base_url=self._docker_host)

    async def _ensure_client(self) -> docker.DockerClient:
        """Ensure Docker client is created (async-safe)."""
        if self._client is None:
            self._client = await asyncio.to_thread(self._create_client)
        return self._client

    async def connect(self) -> bool:
        """Test connection to Docker daemon."""
        try:
            client = await self._ensure_client()
            await asyncio.to_thread(client.ping)
            return True
        except Exception as err:
            _LOGGER.error("Failed to connect to Docker: %s", err)
            return False

    async def get_container(self, container_name: str) -> Container | None:
        """Get container by name."""
        try:
            client = await self._ensure_client()
            return await asyncio.to_thread(
                client.containers.get, container_name
            )
        except NotFound:
            return None
        except Exception as err:
            _LOGGER.error("Error getting container %s: %s", container_name, err)
            return None

    async def get_container_info(self, container_name: str) -> ContainerInfo | None:
        """Get container information."""
        container = await self.get_container(container_name)
        if container is None:
            return None

        return ContainerInfo(
            id=container.id,
            name=container.name,
            image=container.image.tags[0] if container.image.tags else "unknown",
            status=container.status,
            state=self._map_status_to_state(container.status),
            ports=container.ports,
            created=container.attrs.get("Created", ""),
            labels=container.labels,
        )

    async def get_container_stats(self, container_name: str) -> ContainerStats | None:
        """Get container resource usage statistics."""
        container = await self.get_container(container_name)
        if container is None or container.status != "running":
            return None

        try:
            stats = await asyncio.to_thread(container.stats, stream=False)

            # Calculate CPU percentage
            cpu_delta = (
                stats["cpu_stats"]["cpu_usage"]["total_usage"]
                - stats["precpu_stats"]["cpu_usage"]["total_usage"]
            )
            system_delta = (
                stats["cpu_stats"]["system_cpu_usage"]
                - stats["precpu_stats"]["system_cpu_usage"]
            )
            num_cpus = stats["cpu_stats"]["online_cpus"]

            cpu_percent = 0.0
            if system_delta > 0 and cpu_delta > 0:
                cpu_percent = (cpu_delta / system_delta) * num_cpus * 100

            # Memory stats
            memory_usage = stats["memory_stats"].get("usage", 0)
            memory_limit = stats["memory_stats"].get("limit", 1)
            memory_percent = (memory_usage / memory_limit) * 100 if memory_limit else 0

            # Network stats
            networks = stats.get("networks", {})
            network_rx = sum(n.get("rx_bytes", 0) for n in networks.values())
            network_tx = sum(n.get("tx_bytes", 0) for n in networks.values())

            return ContainerStats(
                cpu_percent=round(cpu_percent, 2),
                memory_usage=memory_usage,
                memory_limit=memory_limit,
                memory_percent=round(memory_percent, 2),
                network_rx=network_rx,
                network_tx=network_tx,
            )
        except Exception as err:
            _LOGGER.debug("Error getting stats for %s: %s", container_name, err)
            return None

    async def get_container_state(self, container_name: str) -> str:
        """Get container state as a simple string."""
        container = await self.get_container(container_name)
        if container is None:
            return STATE_NOT_INSTALLED
        return self._map_status_to_state(container.status)

    async def get_project_state(self, project_name: str) -> str:
        """Get state for a Docker Compose project (multi-container stack).

        Checks for containers with the project name prefix (e.g., mediamanager-*).
        Returns running if any container is running, stopped if all are stopped.
        """
        try:
            client = await self._ensure_client()
            # Filter by compose project label
            filters = {"label": f"com.docker.compose.project={project_name}"}
            containers = await asyncio.to_thread(
                client.containers.list, all=True, filters=filters
            )

            if not containers:
                return STATE_NOT_INSTALLED

            # Check states of all containers
            running_count = 0
            for container in containers:
                if container.status == "running":
                    running_count += 1

            if running_count > 0:
                return STATE_RUNNING
            return STATE_STOPPED

        except Exception as err:
            _LOGGER.debug("Error getting project state for %s: %s", project_name, err)
            return STATE_NOT_INSTALLED

    async def list_containers(
        self, all_containers: bool = True, label_filter: str | None = None
    ) -> list[ContainerInfo]:
        """List containers."""
        try:
            client = await self._ensure_client()
            filters = {}
            if label_filter:
                filters["label"] = label_filter

            containers = await asyncio.to_thread(
                client.containers.list, all=all_containers, filters=filters
            )

            return [
                ContainerInfo(
                    id=c.id,
                    name=c.name,
                    image=c.image.tags[0] if c.image.tags else "unknown",
                    status=c.status,
                    state=self._map_status_to_state(c.status),
                    ports=c.ports,
                    created=c.attrs.get("Created", ""),
                    labels=c.labels,
                )
                for c in containers
            ]
        except Exception as err:
            _LOGGER.error("Error listing containers: %s", err)
            return []

    async def start_container(self, container_name: str) -> bool:
        """Start a container."""
        container = await self.get_container(container_name)
        if container is None:
            _LOGGER.error("Container %s not found", container_name)
            return False

        try:
            await asyncio.to_thread(container.start)
            _LOGGER.info("Started container %s", container_name)
            return True
        except Exception as err:
            _LOGGER.error("Error starting container %s: %s", container_name, err)
            return False

    async def stop_container(self, container_name: str, timeout: int = 10) -> bool:
        """Stop a container."""
        container = await self.get_container(container_name)
        if container is None:
            _LOGGER.error("Container %s not found", container_name)
            return False

        try:
            await asyncio.to_thread(container.stop, timeout=timeout)
            _LOGGER.info("Stopped container %s", container_name)
            return True
        except Exception as err:
            _LOGGER.error("Error stopping container %s: %s", container_name, err)
            return False

    async def restart_container(self, container_name: str, timeout: int = 10) -> bool:
        """Restart a container."""
        container = await self.get_container(container_name)
        if container is None:
            _LOGGER.error("Container %s not found", container_name)
            return False

        try:
            await asyncio.to_thread(container.restart, timeout=timeout)
            _LOGGER.info("Restarted container %s", container_name)
            return True
        except Exception as err:
            _LOGGER.error("Error restarting container %s: %s", container_name, err)
            return False

    async def remove_container(
        self, container_name: str, force: bool = True, volumes: bool = False
    ) -> bool:
        """Remove a container."""
        container = await self.get_container(container_name)
        if container is None:
            return True  # Already removed

        try:
            await asyncio.to_thread(container.remove, force=force, v=volumes)
            _LOGGER.info("Removed container %s", container_name)
            return True
        except Exception as err:
            _LOGGER.error("Error removing container %s: %s", container_name, err)
            return False

    async def pull_image(self, image: str) -> bool:
        """Pull a Docker image."""
        try:
            client = await self._ensure_client()
            _LOGGER.info("Pulling image %s", image)
            await asyncio.to_thread(client.images.pull, image)
            _LOGGER.info("Successfully pulled image %s", image)
            return True
        except ImageNotFound:
            _LOGGER.error("Image %s not found", image)
            return False
        except APIError as err:
            _LOGGER.error("Error pulling image %s: %s", image, err)
            return False

    async def get_container_logs(
        self, container_name: str, tail: int = 100
    ) -> str | None:
        """Get container logs."""
        container = await self.get_container(container_name)
        if container is None:
            return None

        try:
            logs = await asyncio.to_thread(
                container.logs, tail=tail, timestamps=True
            )
            return logs.decode("utf-8", errors="replace")
        except Exception as err:
            _LOGGER.error("Error getting logs for %s: %s", container_name, err)
            return None

    async def _recreate_with_image(self, client, existing, new_image: str) -> bool:
        """Recreate a container identically to itself, only swapping the image.

        Reuses the container's OWN live configuration (from docker inspect):
        mounts, environment, user, devices, capabilities, network mode, restart
        policy, exposed/published ports, cmd/entrypoint. This keeps updates
        generic (nothing installation-specific, no compose re-parsing) and
        prevents the config loss that would otherwise break a container after
        an update.

        Env vars, Cmd and Entrypoint baked into the OLD image are not pinned, so
        the NEW image's own defaults win; only user-supplied overrides are kept.

        Returns True on success. On failure the old container has already been
        removed, so the caller should fall back to compose-based creation.
        """
        attrs = existing.attrs
        name = (attrs.get("Name") or "").lstrip("/")
        config = attrs.get("Config", {}) or {}
        host_config = attrs.get("HostConfig", {}) or {}
        networks = (attrs.get("NetworkSettings", {}) or {}).get("Networks", {}) or {}

        # Distinguish user-supplied config from what the OLD image baked in, so
        # the new image can provide its own updated defaults.
        old_image_cfg = {}
        try:
            old_image_cfg = (existing.image.attrs.get("Config", {}) or {})
        except Exception:  # noqa: BLE001 - best effort
            pass
        image_env = old_image_cfg.get("Env", []) or []
        user_env = [e for e in (config.get("Env", []) or []) if e not in image_env]

        cmd = config.get("Cmd")
        if cmd == old_image_cfg.get("Cmd"):
            cmd = None  # inherit from the new image
        entrypoint = config.get("Entrypoint")
        if entrypoint == old_image_cfg.get("Entrypoint"):
            entrypoint = None

        # Ports that must stay exposed for the preserved PortBindings to work.
        exposed_ports = list((config.get("ExposedPorts") or {}).keys())

        # Stop + remove the old container (config already captured above).
        try:
            await asyncio.to_thread(existing.stop, timeout=10)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not stop %s before recreate: %s", name, err)
        await asyncio.to_thread(existing.remove, force=True)

        create_kwargs: dict[str, Any] = {
            "image": new_image,
            "name": name,
            "detach": True,
            "environment": user_env,
            "labels": config.get("Labels") or {},
            "host_config": host_config,
        }
        if exposed_ports:
            create_kwargs["ports"] = exposed_ports
        if cmd is not None:
            create_kwargs["command"] = cmd
        if entrypoint is not None:
            create_kwargs["entrypoint"] = entrypoint
        if config.get("User"):
            create_kwargs["user"] = config["User"]
        if config.get("WorkingDir"):
            create_kwargs["working_dir"] = config["WorkingDir"]
        # Hostname is only settable when not sharing the host/other netns.
        if config.get("Hostname") and host_config.get("NetworkMode", "") not in ("host",):
            create_kwargs["hostname"] = config["Hostname"]

        # Re-attach to a user-defined (non-default) network if there is one.
        first_net = next(iter(networks), None)
        if first_net and first_net not in ("bridge", "host", "none"):
            create_kwargs["networking_config"] = await asyncio.to_thread(
                client.api.create_networking_config,
                {first_net: client.api.create_endpoint_config()},
            )

        _LOGGER.info("Recreating %s from its own config with image %s", name, new_image)
        result = await asyncio.to_thread(client.api.create_container, **create_kwargs)
        await asyncio.to_thread(client.api.start, result["Id"])
        _LOGGER.info("Container %s recreated and started", name)
        return True

    async def compose_up(
        self,
        compose_file: Path,
        project_name: str | None = None,
        detach: bool = True,
        pull: bool = True,
        recreate: bool = False,
    ) -> bool:
        """Create and start container from compose file using Docker SDK.

        When recreate=True (used by updates), an existing container is stopped
        and removed before being recreated, so the freshly pulled image and the
        restart policy are actually applied. Without it, an update would only
        restart the old container with the old image and could leave it stopped.
        """
        try:
            client = await self._ensure_client()

            # Parse compose file (in thread to avoid blocking)
            compose_data = await asyncio.to_thread(self._read_yaml_file, compose_file)

            services = compose_data.get("services", {})
            if not services:
                _LOGGER.error("No services found in compose file")
                return False

            for service_name, service_config in services.items():
                image = service_config.get("image")
                if not image:
                    _LOGGER.error("No image specified for service %s", service_name)
                    continue

                container_name = service_config.get("container_name", f"{project_name}-{service_name}-1")

                # Pull image if requested
                if pull:
                    _LOGGER.info("Pulling image %s", image)
                    try:
                        await asyncio.to_thread(client.images.pull, image)
                    except Exception as err:
                        _LOGGER.warning("Failed to pull image %s: %s", image, err)

                # Check if container already exists
                try:
                    existing = await asyncio.to_thread(client.containers.get, container_name)
                    if recreate:
                        # Update path: recreate the container from its OWN live
                        # config (inspect) with only the image swapped, so every
                        # runtime setting is preserved. Falls back to building
                        # from the compose file if that fails.
                        try:
                            if await self._recreate_with_image(client, existing, image):
                                continue
                        except Exception as err:  # noqa: BLE001
                            _LOGGER.warning(
                                "Inspect-based recreate of %s failed (%s); "
                                "falling back to compose-based create",
                                container_name, err,
                            )
                        # container is now removed -> fall through to create below
                    else:
                        if existing.status != "running":
                            await asyncio.to_thread(existing.start)
                            _LOGGER.info("Started existing container %s", container_name)
                        continue
                except NotFound:
                    pass

                # Build container config
                container_config = {
                    "image": image,
                    "name": container_name,
                    "detach": detach,
                    "labels": service_config.get("labels", {}),
                }

                # Restart policy
                restart = service_config.get("restart", "no")
                if restart and restart != "no":
                    container_config["restart_policy"] = {"Name": restart.replace("unless-stopped", "unless-stopped")}

                # Ports
                if "ports" in service_config:
                    ports = {}
                    for port_mapping in service_config["ports"]:
                        if isinstance(port_mapping, str):
                            parts = port_mapping.split(":")
                            if len(parts) == 2:
                                host_port, container_port = parts
                                container_config.setdefault("ports", {})[container_port] = int(host_port)
                    if ports:
                        container_config["ports"] = ports

                # Volumes
                if "volumes" in service_config:
                    volumes = {}
                    for vol in service_config["volumes"]:
                        if isinstance(vol, str) and ":" in vol:
                            parts = vol.split(":")
                            host_path = parts[0]
                            container_path = parts[1]
                            mode = parts[2] if len(parts) > 2 else "rw"
                            volumes[host_path] = {"bind": container_path, "mode": mode}
                    if volumes:
                        container_config["volumes"] = volumes

                # Environment
                if "environment" in service_config:
                    env = service_config["environment"]
                    if isinstance(env, dict):
                        container_config["environment"] = env
                    elif isinstance(env, list):
                        container_config["environment"] = env

                # Network mode
                if "network_mode" in service_config:
                    container_config["network_mode"] = service_config["network_mode"]

                # Privileged
                if service_config.get("privileged"):
                    container_config["privileged"] = True

                # Devices
                if "devices" in service_config:
                    container_config["devices"] = service_config["devices"]

                # Cap add
                if "cap_add" in service_config:
                    container_config["cap_add"] = service_config["cap_add"]

                _LOGGER.info("Creating container %s from image %s", container_name, image)
                container = await asyncio.to_thread(client.containers.run, **container_config)
                _LOGGER.info("Container %s created and started", container_name)

            return True
        except Exception as err:
            _LOGGER.error("Error in compose_up: %s", err)
            return False

    async def compose_down(
        self,
        compose_file: Path,
        project_name: str | None = None,
        volumes: bool = False,
        remove_orphans: bool = True,
    ) -> bool:
        """Stop and remove container using Docker SDK."""
        try:
            client = await self._ensure_client()

            # Parse compose file (in thread to avoid blocking)
            compose_data = await asyncio.to_thread(self._read_yaml_file, compose_file)

            services = compose_data.get("services", {})

            for service_name, service_config in services.items():
                container_name = service_config.get("container_name", f"{project_name}-{service_name}-1")

                try:
                    container = await asyncio.to_thread(client.containers.get, container_name)
                    _LOGGER.info("Stopping container %s", container_name)
                    await asyncio.to_thread(container.stop, timeout=10)
                    _LOGGER.info("Removing container %s", container_name)
                    await asyncio.to_thread(container.remove, v=volumes)
                    _LOGGER.info("Container %s removed", container_name)
                except NotFound:
                    _LOGGER.debug("Container %s not found", container_name)
                except Exception as err:
                    _LOGGER.error("Error removing container %s: %s", container_name, err)
                    return False

            return True
        except Exception as err:
            _LOGGER.error("Error in compose_down: %s", err)
            return False

    async def compose_pull(self, compose_file: Path, project_name: str | None = None) -> bool:
        """Pull images from compose file using Docker SDK."""
        try:
            client = await self._ensure_client()

            # Parse compose file (in thread to avoid blocking)
            compose_data = await asyncio.to_thread(self._read_yaml_file, compose_file)

            services = compose_data.get("services", {})

            for service_name, service_config in services.items():
                image = service_config.get("image")
                if image:
                    _LOGGER.info("Pulling image %s", image)
                    await asyncio.to_thread(client.images.pull, image)

            return True
        except Exception as err:
            _LOGGER.error("Error in compose_pull: %s", err)
            return False

    def _map_status_to_state(self, status: str) -> str:
        """Map Docker status to our state constants."""
        status_lower = status.lower()
        if status_lower == "running":
            return STATE_RUNNING
        if status_lower in ("exited", "stopped", "dead", "created"):
            return STATE_STOPPED
        return STATE_ERROR

    def _read_yaml_file(self, file_path: Path) -> dict:
        """Read and parse a YAML file (blocking, run in thread)."""
        import yaml
        with open(file_path, "r") as f:
            return yaml.safe_load(f)

    def close(self) -> None:
        """Close the Docker client connection."""
        if self._client:
            self._client.close()
            self._client = None

    async def get_local_image_digest(self, image_name: str) -> str | None:
        """Get local image digest via Docker inspect."""
        try:
            client = await self._ensure_client()
            image = await asyncio.to_thread(client.images.get, image_name)
            # RepoDigests contains the digest for pulled images
            repo_digests = image.attrs.get("RepoDigests", [])
            if repo_digests:
                # Format: registry/repo@sha256:xxx
                digest = repo_digests[0].split("@")[-1]
                return digest
            # Fallback to image ID if no repo digest
            return image.id
        except ImageNotFound:
            return None
        except Exception as err:
            _LOGGER.debug("Error getting local digest for %s: %s", image_name, err)
            return None

    async def get_remote_image_digest(self, image: str) -> str | None:
        """Get remote image digest via Docker Registry V2 API."""
        try:
            registry, repository, tag = self._parse_image_name(image)

            # Get auth token (returns empty string for registries without auth)
            token = await self._get_registry_token(registry, repository)
            if token is None:
                _LOGGER.debug("Failed to get token for %s", image)
                return None

            # Get manifest digest
            digest = await self._get_manifest_digest(registry, repository, tag, token)
            return digest

        except Exception as err:
            _LOGGER.debug("Error getting remote digest for %s: %s", image, err)
            return None

    def _parse_image_name(self, image: str) -> tuple[str, str, str]:
        """Parse image name into registry, repository, and tag."""
        # Default values
        registry = "registry-1.docker.io"
        tag = "latest"

        # Remove tag if present
        if ":" in image and "@" not in image:
            image, tag = image.rsplit(":", 1)
        elif "@" in image:
            # Handle digest format
            image = image.split("@")[0]

        # Check for registry prefix
        parts = image.split("/")
        if len(parts) == 1:
            # Official image: nginx -> library/nginx
            repository = f"library/{parts[0]}"
        elif len(parts) == 2:
            if "." in parts[0] or ":" in parts[0]:
                # Custom registry: ghcr.io/owner/repo
                registry = parts[0]
                repository = parts[1]
            else:
                # Docker Hub user image: user/repo
                repository = image
        else:
            # Custom registry with path: registry.example.com/path/to/repo
            registry = parts[0]
            repository = "/".join(parts[1:])

        # Map common registries
        if registry == "docker.io":
            registry = "registry-1.docker.io"

        return registry, repository, tag

    async def _get_registry_token(self, registry: str, repository: str) -> str | None:
        """Get authentication token for registry."""
        try:
            async with aiohttp.ClientSession() as session:
                if registry == "registry-1.docker.io":
                    # Docker Hub auth
                    auth_url = (
                        f"https://auth.docker.io/token"
                        f"?service=registry.docker.io"
                        f"&scope=repository:{repository}:pull"
                    )
                elif registry == "ghcr.io":
                    # GitHub Container Registry
                    auth_url = (
                        f"https://ghcr.io/token"
                        f"?service=ghcr.io"
                        f"&scope=repository:{repository}:pull"
                    )
                elif registry == "lscr.io":
                    # LinuxServer registry (uses ghcr.io backend)
                    # Repository is already prefixed with linuxserver/
                    auth_url = (
                        f"https://ghcr.io/token"
                        f"?service=ghcr.io"
                        f"&scope=repository:{repository}:pull"
                    )
                else:
                    # Generic registry - try without auth first
                    return ""

                async with session.get(auth_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("token")
                    return None
        except Exception as err:
            _LOGGER.debug("Error getting registry token: %s", err)
            return None

    async def _get_manifest_digest(
        self, registry: str, repository: str, tag: str, token: str
    ) -> str | None:
        """Get manifest digest from registry."""
        try:
            if registry == "registry-1.docker.io":
                url = f"https://registry-1.docker.io/v2/{repository}/manifests/{tag}"
            elif registry == "ghcr.io":
                url = f"https://ghcr.io/v2/{repository}/manifests/{tag}"
            elif registry == "lscr.io":
                # Repository is already prefixed with linuxserver/
                url = f"https://ghcr.io/v2/{repository}/manifests/{tag}"
            else:
                url = f"https://{registry}/v2/{repository}/manifests/{tag}"

            headers = {
                "Accept": "application/vnd.docker.distribution.manifest.v2+json, "
                          "application/vnd.oci.image.manifest.v1+json"
            }
            if token:
                headers["Authorization"] = f"Bearer {token}"

            async with aiohttp.ClientSession() as session:
                async with session.head(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        return resp.headers.get("Docker-Content-Digest")
                    _LOGGER.debug(
                        "Failed to get manifest for %s/%s:%s - status %d",
                        registry, repository, tag, resp.status
                    )
                    return None
        except Exception as err:
            _LOGGER.debug("Error getting manifest digest: %s", err)
            return None
