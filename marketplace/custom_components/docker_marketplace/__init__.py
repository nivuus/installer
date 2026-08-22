"""Docker Marketplace integration for Home Assistant."""
from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .catalog import Catalog
from .const import (
    ATTR_APP_ID,
    ATTR_CONFIG,
    ATTR_REMOVE_DATA,
    CONF_CATALOG_PATH,
    CONF_DOCKER_HOST,
    CONF_STACKS_PATH,
    DEFAULT_CATALOG_PATH,
    DEFAULT_DOCKER_HOST,
    DEFAULT_STACKS_PATH,
    DOMAIN,
    PLATFORMS,
    SERVICE_INSTALL_APP,
    SERVICE_REMOVE_APP,
    SERVICE_RESTART_APP,
    SERVICE_START_APP,
    SERVICE_STOP_APP,
    SERVICE_UPDATE_APP,
)
from .coordinator import DockerMarketplaceCoordinator
from .docker_client import DockerClient

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up Docker Marketplace from configuration.yaml."""
    hass.data.setdefault(DOMAIN, {})

    # Register the frontend panel
    await _async_register_panel(hass)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Docker Marketplace from a config entry."""
    _LOGGER.info("Setting up Docker Marketplace integration")

    # Get configuration
    docker_host = entry.data.get(CONF_DOCKER_HOST, DEFAULT_DOCKER_HOST)
    catalog_path = entry.data.get(CONF_CATALOG_PATH, DEFAULT_CATALOG_PATH)
    stacks_path = entry.data.get(CONF_STACKS_PATH, DEFAULT_STACKS_PATH)

    # Create Docker client
    docker_client = DockerClient(docker_host)

    # Test Docker connection
    if not await docker_client.connect():
        _LOGGER.error("Failed to connect to Docker at %s", docker_host)
        return False

    # Create and load catalog
    catalog = Catalog(catalog_path)
    await catalog.load()

    # Create coordinator
    coordinator = DockerMarketplaceCoordinator(
        hass=hass,
        docker_client=docker_client,
        catalog=catalog,
        stacks_path=stacks_path,
    )

    # Initial data fetch
    await coordinator.async_config_entry_first_refresh()

    # Store data
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "docker_client": docker_client,
        "catalog": catalog,
        "stacks_path": stacks_path,
    }

    # Register services
    await _async_register_services(hass, coordinator)

    # Register WebSocket API
    await _async_register_websocket_api(hass, coordinator, catalog)

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("Docker Marketplace setup complete")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        # Close Docker client
        data["docker_client"].close()

        # Services are domain-wide: only drop them once the last entry is gone
        if not hass.config_entries.async_loaded_entries(DOMAIN):
            _async_unregister_services(hass)
            hass.data[DOMAIN].pop("coordinator", None)
            hass.data[DOMAIN].pop("catalog", None)

    return unload_ok


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Register the frontend panel."""
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.components.frontend import async_register_built_in_panel

    # Path to frontend files
    frontend_path = Path(__file__).parent / "frontend"

    if frontend_path.exists():
        # Register static path for frontend files (new API)
        await hass.http.async_register_static_paths([
            StaticPathConfig(
                url_path=f"/{DOMAIN}/frontend",
                path=str(frontend_path),
                cache_headers=False,
            )
        ])

        # Register the panel
        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="Store",
            sidebar_icon="mdi:store",
            frontend_url_path="store",
            config={
                "_panel_custom": {
                    "name": "docker-marketplace-panel",
                    "module_url": f"/{DOMAIN}/frontend/main.js",
                }
            },
            require_admin=True,
        )
        _LOGGER.info("Registered Docker Marketplace panel")
    else:
        _LOGGER.warning("Frontend files not found at %s", frontend_path)


APP_ID_SCHEMA = vol.Schema({vol.Required(ATTR_APP_ID): cv.string})

SERVICE_SCHEMAS: dict[str, vol.Schema] = {
    SERVICE_INSTALL_APP: vol.Schema(
        {
            vol.Required(ATTR_APP_ID): cv.string,
            vol.Optional(ATTR_CONFIG, default=dict): dict,
        }
    ),
    SERVICE_REMOVE_APP: vol.Schema(
        {
            vol.Required(ATTR_APP_ID): cv.string,
            vol.Optional(ATTR_REMOVE_DATA, default=False): cv.boolean,
        }
    ),
    SERVICE_UPDATE_APP: APP_ID_SCHEMA,
    SERVICE_START_APP: APP_ID_SCHEMA,
    SERVICE_STOP_APP: APP_ID_SCHEMA,
    SERVICE_RESTART_APP: APP_ID_SCHEMA,
}


async def _async_register_services(
    hass: HomeAssistant, coordinator: DockerMarketplaceCoordinator
) -> None:
    """Register integration services."""
    from .services import (
        async_install_app,
        async_remove_app,
        async_update_app,
        async_start_app,
        async_stop_app,
        async_restart_app,
    )

    handlers = {
        SERVICE_INSTALL_APP: async_install_app,
        SERVICE_REMOVE_APP: async_remove_app,
        SERVICE_UPDATE_APP: async_update_app,
        SERVICE_START_APP: async_start_app,
        SERVICE_STOP_APP: async_stop_app,
        SERVICE_RESTART_APP: async_restart_app,
    }

    # Bind hass/coordinator with partial: HassJob unwraps functools.partial to
    # detect the coroutine function. A lambda would be classified as a sync
    # handler, run in the executor, and its coroutine silently never awaited.
    for service, handler in handlers.items():
        hass.services.async_register(
            DOMAIN,
            service,
            partial(handler, hass, coordinator),
            schema=SERVICE_SCHEMAS[service],
        )

    _LOGGER.info("Registered Docker Marketplace services")


def _async_unregister_services(hass: HomeAssistant) -> None:
    """Remove integration services."""
    for service in SERVICE_SCHEMAS:
        hass.services.async_remove(DOMAIN, service)


async def _async_register_websocket_api(
    hass: HomeAssistant,
    coordinator: DockerMarketplaceCoordinator,
    catalog: Catalog,
) -> None:
    """Register WebSocket API commands."""
    from .api import (
        websocket_get_catalog,
        websocket_get_installed,
        websocket_install_app,
        websocket_remove_app,
        websocket_start_app,
        websocket_stop_app,
        websocket_restart_app,
        websocket_get_logs,
        websocket_get_app_status,
        websocket_get_config,
        websocket_update_config,
    )

    from homeassistant.components import websocket_api

    # Store coordinator and catalog in hass.data for API access
    hass.data[DOMAIN]["coordinator"] = coordinator
    hass.data[DOMAIN]["catalog"] = catalog

    websocket_api.async_register_command(hass, websocket_get_catalog)
    websocket_api.async_register_command(hass, websocket_get_installed)
    websocket_api.async_register_command(hass, websocket_install_app)
    websocket_api.async_register_command(hass, websocket_remove_app)
    websocket_api.async_register_command(hass, websocket_start_app)
    websocket_api.async_register_command(hass, websocket_stop_app)
    websocket_api.async_register_command(hass, websocket_restart_app)
    websocket_api.async_register_command(hass, websocket_get_logs)
    websocket_api.async_register_command(hass, websocket_get_app_status)
    websocket_api.async_register_command(hass, websocket_get_config)
    websocket_api.async_register_command(hass, websocket_update_config)

    _LOGGER.info("Registered Docker Marketplace WebSocket API")
