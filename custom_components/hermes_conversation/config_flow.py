"""Config flow for Hermes Conversation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
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

ADDON_CONFIGS_ROOT = Path("/addon_configs")

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
        """Handle the initial step — offer menu if addon is discovered."""
        # Try auto-discovery
        discovered = await self._async_discover_addon()
        if discovered:
            return self.async_show_menu(
                step_id="user",
                menu_options=["confirm", "manual"],
            )

        # No addon found — go straight to manual
        return await self.async_step_manual(user_input)

    async def _async_discover_addon(self) -> bool:
        """Discover the Hermes Agent add-on by scanning /addon_configs/.

        The addon slug has a repo-hash prefix (e.g. a0d7b954_hermes_agent)
        that changes per installation.  We scan the filesystem for any
        directory ending in '_hermes_agent' or named 'hermes_agent', then
        derive the Docker hostname from the directory name.
        """
        try:
            if not ADDON_CONFIGS_ROOT.is_dir():
                _LOGGER.debug("No /addon_configs/ — not running on HA OS")
                return False

            # Find the addon config directory
            addon_dir = self._find_addon_config_dir()
            if addon_dir is None:
                _LOGGER.debug("Hermes Agent add-on config directory not found")
                return False

            # Derive Docker hostname from directory name
            # e.g. "a0d7b954_hermes_agent" → "a0d7b954-hermes-agent"
            hostname = addon_dir.name.replace("_", "-")

            # Try to read addon options for API key
            api_key = self._read_addon_api_key(addon_dir)

            self._discovered_host = hostname
            self._discovered_port = ADDON_INTERNAL_PORT
            self._discovered_api_key = api_key

            _LOGGER.info(
                "Discovered Hermes Agent add-on at %s:%s",
                self._discovered_host,
                self._discovered_port,
            )
            return True

        except Exception:
            _LOGGER.debug("Add-on discovery failed", exc_info=True)
            return False

    @staticmethod
    def _find_addon_config_dir() -> Path | None:
        """Find the Hermes Agent addon directory in /addon_configs/."""
        try:
            for entry in ADDON_CONFIGS_ROOT.iterdir():
                if not entry.is_dir():
                    continue
                name = entry.name
                if (
                    name == ADDON_SLUG_SUFFIX
                    or name.endswith(f"_{ADDON_SLUG_SUFFIX}")
                ):
                    return entry
            return None
        except OSError:
            return None

    @staticmethod
    def _read_addon_api_key(addon_dir: Path) -> str | None:
        """Read the access_password from the addon's options.json."""
        try:
            import json

            options_file = addon_dir / "options.json"
            if not options_file.is_file():
                return None
            options = json.loads(options_file.read_text())
            return options.get("access_password", "") or None
        except Exception:
            return None

    def _abort_if_host_port_configured(self, host: str, port: int) -> None:
        """Abort if an entry with the same host:port already exists."""
        for entry in self._async_current_entries():
            if entry.data.get(CONF_HOST) == host and entry.data.get(CONF_PORT) == port:
                raise AbortFlow("already_configured")

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
                use_ssl=False,
                verify_ssl=False,
            )
            try:
                await client.async_check_connection()
                self._abort_if_host_port_configured(
                    self._discovered_host, self._discovered_port,
                )
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
                self._abort_if_host_port_configured(host, port)
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
