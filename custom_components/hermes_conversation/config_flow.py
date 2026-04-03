"""Config flow for Hermes Conversation."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HermesApiClient, HermesAuthError, HermesConnectionError
from .const import (
    ADDON_INTERNAL_PORT,
    ADDON_SLUG_SUFFIX,
    CONF_API_KEY,
    CONF_CONTEXT_MAX_CHARS,
    CONF_HOST,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    CONF_INCLUDE_EXPOSED_ENTITIES,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_PORT,
    CONF_PROMPT,
    CONF_TEMPERATURE,
    DEFAULT_CONTEXT_MAX_CHARS,
    DEFAULT_INCLUDE_EXPOSED_ENTITIES,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_PORT,
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class HermesConversationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hermes Conversation."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the config flow."""
        self._discovered_host: str | None = None
        self._discovered_port: int | None = None
        self._discovered_api_key: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return HermesConversationOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle the initial step — attempt addon auto-discovery."""
        # Only allow one instance
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        # Try auto-discovery via Supervisor
        discovered = await self._async_discover_addon()
        if discovered:
            return await self.async_step_confirm()

        # Fall back to manual
        return await self.async_step_manual(user_input)

    async def _async_discover_addon(self) -> bool:
        """Try to discover the Hermes Agent add-on via the Supervisor API."""
        try:
            # Check if hassio integration is available
            if "hassio" not in self.hass.data:
                return False

            # Try to find the addon — the slug has a repo hash prefix
            # e.g. "a0d7b954_hermes_agent"
            addon_info = await self._async_find_addon()
            if addon_info is None:
                return False

            # Check addon is running
            if addon_info.get("state") != "started":
                _LOGGER.debug("Hermes Agent add-on is not running")
                return False

            # Check API is enabled
            options = addon_info.get("options", {})
            if not options.get("enable_api", False):
                _LOGGER.debug("Hermes Agent API is not enabled in add-on config")
                return False

            # Get hostname and API key
            hostname = addon_info.get("hostname", "")
            if not hostname:
                return False

            self._discovered_host = hostname
            self._discovered_port = ADDON_INTERNAL_PORT
            self._discovered_api_key = options.get("access_password", "") or None

            _LOGGER.info(
                "Discovered Hermes Agent add-on at %s:%s",
                self._discovered_host,
                self._discovered_port,
            )
            return True

        except Exception:
            _LOGGER.debug("Add-on discovery failed", exc_info=True)
            return False

    async def _async_find_addon(self) -> dict[str, Any] | None:
        """Find the Hermes Agent addon among installed addons."""
        try:
            from homeassistant.components.hassio import async_get_addon_info

            # Try common slug patterns
            for slug in [
                ADDON_SLUG_SUFFIX,
                f"local_{ADDON_SLUG_SUFFIX}",
            ]:
                try:
                    info = await async_get_addon_info(self.hass, slug)
                    if info is not None:
                        return info
                except Exception:
                    continue

            # If direct slugs don't work, try the Supervisor REST API
            hassio = self.hass.data.get("hassio")
            if hassio is None:
                return None

            result = await hassio.send_command("/addons", method="get")
            if not result or "data" not in result:
                return None

            addons = result["data"].get("addons", [])
            for addon in addons:
                slug = addon.get("slug", "")
                if slug.endswith(f"_{ADDON_SLUG_SUFFIX}") or slug == ADDON_SLUG_SUFFIX:
                    # Found it — get full info
                    try:
                        return await async_get_addon_info(self.hass, slug)
                    except Exception:
                        return addon

            return None

        except Exception:
            _LOGGER.debug("Failed to query addons", exc_info=True)
            return None

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Confirm the discovered addon connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate the discovered connection
            session = async_get_clientsession(self.hass)
            client = HermesApiClient(
                session,
                self._discovered_host,
                self._discovered_port,
                self._discovered_api_key,
            )
            try:
                await client.async_check_connection()
                return self.async_create_entry(
                    title="Hermes Conversation",
                    data={
                        CONF_HOST: self._discovered_host,
                        CONF_PORT: self._discovered_port,
                        CONF_API_KEY: self._discovered_api_key or "",
                        CONF_USE_SSL: False,
                        CONF_VERIFY_SSL: False,
                    },
                )
            except HermesAuthError:
                errors["base"] = "invalid_auth"
            except HermesConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during connection validation")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "host": self._discovered_host or "",
                "port": str(self._discovered_port or ADDON_INTERNAL_PORT),
            },
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle manual configuration entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            api_key = user_input.get(CONF_API_KEY, "") or None

            use_ssl = user_input.get(CONF_USE_SSL, True)
            verify_ssl = user_input.get(CONF_VERIFY_SSL, False)

            session = async_get_clientsession(self.hass)
            client = HermesApiClient(
                session, host, port, api_key,
                use_ssl=use_ssl, verify_ssl=verify_ssl,
            )

            try:
                await client.async_check_connection()
                return self.async_create_entry(
                    title="Hermes Conversation",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_API_KEY: api_key or "",
                        CONF_USE_SSL: use_ssl,
                        CONF_VERIFY_SSL: verify_ssl,
                    },
                )
            except HermesAuthError:
                errors["base"] = "invalid_auth"
            except HermesConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during connection validation")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default="homeassistant.local"): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                    vol.Optional(CONF_API_KEY, default=""): str,
                    vol.Optional(CONF_USE_SSL, default=True): bool,
                    vol.Optional(CONF_VERIFY_SSL, default=False): bool,
                }
            ),
            errors=errors,
        )


class HermesConversationOptionsFlow(OptionsFlow):
    """Handle options for Hermes Conversation."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_MODEL,
                        default=options.get(CONF_MODEL, DEFAULT_MODEL),
                    ): str,
                    vol.Optional(
                        CONF_PROMPT,
                        default=options.get(CONF_PROMPT, DEFAULT_PROMPT),
                    ): str,
                    vol.Optional(
                        CONF_TEMPERATURE,
                        default=options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=2.0)),
                    vol.Optional(
                        CONF_MAX_TOKENS,
                        default=options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=128000)),
                    vol.Optional(
                        CONF_INCLUDE_EXPOSED_ENTITIES,
                        default=options.get(
                            CONF_INCLUDE_EXPOSED_ENTITIES,
                            DEFAULT_INCLUDE_EXPOSED_ENTITIES,
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_CONTEXT_MAX_CHARS,
                        default=options.get(
                            CONF_CONTEXT_MAX_CHARS, DEFAULT_CONTEXT_MAX_CHARS
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1000, max=200000)),
                }
            ),
        )
