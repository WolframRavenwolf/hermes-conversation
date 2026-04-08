"""The Hermes Conversation integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HermesApiClient
from .const import CONF_API_KEY, CONF_HOST, CONF_PORT, CONF_USE_SSL, CONF_VERIFY_SSL, DOMAIN

_LOGGER = logging.getLogger(__name__)
_PLATFORMS: tuple[Platform, ...] = (Platform.CONVERSATION,)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hermes Conversation from a config entry."""
    session = async_get_clientsession(hass)

    client = HermesApiClient(
        session=session,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        api_key=entry.data.get(CONF_API_KEY) or None,
        use_ssl=entry.data.get(CONF_USE_SSL, True),
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, False),
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
    }

    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("Hermes Conversation set up successfully")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update — reload the integration."""
    await hass.config_entries.async_reload(entry.entry_id)
