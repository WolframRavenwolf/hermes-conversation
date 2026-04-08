"""Conversation agent for Hermes Agent."""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

from homeassistant.components.conversation import (
    AbstractConversationAgent,
    ConversationInput,
    ConversationResult,
    MATCH_ALL,
)
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent, template

from .api import HermesApiClient, HermesApiError
from .const import (
    CONF_CONTEXT_MAX_CHARS,
    CONF_INCLUDE_EXPOSED_ENTITIES,
    CONF_PROMPT,
    DEFAULT_CONTEXT_MAX_CHARS,
    DEFAULT_INCLUDE_EXPOSED_ENTITIES,
    DEFAULT_MAX_HISTORY_MESSAGES,
    DEFAULT_PROMPT,
)
from .tool_trace_filter import (
    append_tool_trace_prompt,
    sanitize_response_text,
    should_hide_tool_traces,
)

_LOGGER = logging.getLogger(__name__)
_MAX_CACHED_CONVERSATIONS = 50


class HermesConversationAgent(AbstractConversationAgent):
    """Hermes Agent conversation agent for Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: HermesApiClient,
    ) -> None:
        """Initialise the conversation agent."""
        self.hass = hass
        self.entry = entry
        self.client = client
        # conversation_id -> list of {"role": ..., "content": ...}
        self._history: OrderedDict[str, list[dict[str, str]]] = OrderedDict()

    @property
    def supported_languages(self) -> list[str] | str:
        """Return supported languages (all — the LLM handles it)."""
        return MATCH_ALL

    async def async_process(
        self, user_input: ConversationInput
    ) -> ConversationResult:
        """Process a conversation turn."""
        try:
            return await self._async_process_inner(user_input)
        except Exception:
            _LOGGER.exception("Unexpected error in async_process")
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                "An internal error occurred. Check the logs.",
            )
            return ConversationResult(
                response=intent_response,
                conversation_id=user_input.conversation_id or "default",
            )

    async def _async_process_inner(
        self, user_input: ConversationInput
    ) -> ConversationResult:
        """Inner processing — wrapped by async_process for error logging."""
        options = self.entry.options

        user_name = await self._get_user_name(user_input)
        system_prompt = self._render_system_prompt(options, user_name)

        extra = getattr(user_input, "extra_system_prompt", None)
        if extra:
            system_prompt = (system_prompt + "\n\n" + extra) if system_prompt else extra

        conv_id = user_input.conversation_id or "default"
        history = self._history.setdefault(conv_id, [])

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": user_input.text})

        try:
            if should_hide_tool_traces(options):
                response_text = await self._get_full_response(messages)
            else:
                response_text = await self._get_response(messages)
        except HermesApiError as err:
            _LOGGER.error("Hermes API error: %s", err)
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Error communicating with Hermes Agent: {err}",
            )
            return ConversationResult(
                response=intent_response,
                conversation_id=conv_id,
            )

        if should_hide_tool_traces(options):
            response_text = sanitize_response_text(response_text)

        history.append({"role": "user", "content": user_input.text})
        history.append({"role": "assistant", "content": response_text})

        while len(history) > DEFAULT_MAX_HISTORY_MESSAGES:
            history.pop(0)
            if history and history[0]["role"] == "assistant":
                history.pop(0)

        while len(self._history) > _MAX_CACHED_CONVERSATIONS:
            self._history.popitem(last=False)

        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(response_text)

        return ConversationResult(
            response=intent_response,
            conversation_id=conv_id,
        )

    async def _get_response(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        """Get a response from the API, trying streaming first."""
        try:
            chunks: list[str] = []
            async for delta in self.client.async_stream_message(messages):
                chunks.append(delta)

            if chunks:
                return "".join(chunks)
        except HermesApiError:
            _LOGGER.debug("Streaming failed, falling back to non-streaming")

        return await self.client.async_send_message(messages)

    async def _get_full_response(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        """Get a complete response from the API without streaming."""
        return await self.client.async_send_message(messages)

    async def _get_user_name(self, user_input: ConversationInput) -> str:
        """Resolve the display name of the user from HA auth."""
        try:
            context = getattr(user_input, "context", None)
            if context is None:
                return "the user"
            user_id = getattr(context, "user_id", None)
            if not user_id:
                return "the user"
            user = await self.hass.auth.async_get_user(user_id)
            if user and user.name:
                return user.name
        except Exception:
            _LOGGER.debug("Could not resolve username", exc_info=True)
        return "the user"

    def _render_system_prompt(self, options: dict[str, Any], user_name: str) -> str:
        """Render the system prompt template with HA context."""
        prompt_template = options.get(CONF_PROMPT, DEFAULT_PROMPT)
        if not prompt_template:
            return append_tool_trace_prompt(options, "")

        variables: dict[str, Any] = {
            "ha_name": self.hass.config.location_name,
            "user_name": user_name,
        }

        include_entities = options.get(
            CONF_INCLUDE_EXPOSED_ENTITIES, DEFAULT_INCLUDE_EXPOSED_ENTITIES
        )
        if include_entities:
            variables["exposed_entities"] = self._get_exposed_entities(options)
        else:
            variables["exposed_entities"] = []

        try:
            tpl = template.Template(prompt_template, self.hass)
            rendered_prompt = tpl.async_render(variables)
        except template.TemplateError as err:
            _LOGGER.warning("System prompt template error: %s", err)
            rendered_prompt = prompt_template

        return append_tool_trace_prompt(options, rendered_prompt)

    def _get_exposed_entities(
        self, options: dict[str, Any]
    ) -> list[dict[str, str]]:
        """Get a list of entities exposed to the conversation agent."""
        max_chars = options.get(CONF_CONTEXT_MAX_CHARS, DEFAULT_CONTEXT_MAX_CHARS)
        entities: list[dict[str, str]] = []
        total_chars = 0

        for state in self.hass.states.async_all():
            try:
                if not async_should_expose(
                    self.hass, "conversation", state.entity_id
                ):
                    continue
            except Exception:
                continue

            entity_info = {
                "entity_id": state.entity_id,
                "name": state.attributes.get("friendly_name", state.entity_id),
                "state": str(state.state),
            }

            line = (
                f"- {entity_info['entity_id']} "
                f"({entity_info['name']}): {entity_info['state']}"
            )
            total_chars += len(line) + 1

            if total_chars > max_chars:
                break

            entities.append(entity_info)

        return entities
