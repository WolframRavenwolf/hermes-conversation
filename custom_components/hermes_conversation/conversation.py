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
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_TEMPERATURE,
    DEFAULT_CONTEXT_MAX_CHARS,
    DEFAULT_INCLUDE_EXPOSED_ENTITIES,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MAX_HISTORY_MESSAGES,
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


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
        options = self.entry.options
        model = options.get(CONF_MODEL, DEFAULT_MODEL)
        temperature = options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)
        max_tokens = options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)

        # Build system prompt
        system_prompt = self._render_system_prompt(options)

        # Append extra system prompt from HA voice pipeline if present
        extra = getattr(user_input, "extra_system_prompt", None)
        if extra:
            system_prompt += "\n\n" + extra

        # Get or create conversation history
        conv_id = user_input.conversation_id or "default"
        history = self._history.setdefault(conv_id, [])

        # Build messages: system + history + new user message
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input.text})

        # Call the API — try streaming first, fall back to non-streaming
        try:
            response_text = await self._get_response(
                messages, model, temperature, max_tokens
            )
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

        # Update conversation history
        history.append({"role": "user", "content": user_input.text})
        history.append({"role": "assistant", "content": response_text})

        # Trim history if too long
        while len(history) > DEFAULT_MAX_HISTORY_MESSAGES:
            # Remove oldest user/assistant pair
            history.pop(0)
            if history and history[0]["role"] == "assistant":
                history.pop(0)

        # Evict oldest conversations if we have too many
        while len(self._history) > 50:
            self._history.popitem(last=False)

        # Build response
        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(response_text)

        return ConversationResult(
            response=intent_response,
            conversation_id=conv_id,
        )

    async def _get_response(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Get a response from the API, trying streaming first."""
        # Try streaming for lower TTFB
        try:
            chunks: list[str] = []
            async for delta in self.client.async_stream_message(
                messages, model, temperature, max_tokens
            ):
                chunks.append(delta)

            if chunks:
                return "".join(chunks)
        except HermesApiError:
            _LOGGER.debug("Streaming failed, falling back to non-streaming")

        # Fall back to non-streaming
        return await self.client.async_send_message(
            messages, model, temperature, max_tokens
        )

    def _render_system_prompt(self, options: dict[str, Any]) -> str:
        """Render the system prompt template with HA context."""
        prompt_template = options.get(CONF_PROMPT, DEFAULT_PROMPT)

        # Build template variables
        variables: dict[str, Any] = {
            "ha_name": self.hass.config.location_name,
        }

        # Include exposed entities if enabled
        include_entities = options.get(
            CONF_INCLUDE_EXPOSED_ENTITIES, DEFAULT_INCLUDE_EXPOSED_ENTITIES
        )
        if include_entities:
            variables["exposed_entities"] = self._get_exposed_entities(options)
        else:
            variables["exposed_entities"] = []

        # Render with HA's template engine
        try:
            tpl = template.Template(prompt_template, self.hass)
            return tpl.async_render(variables)
        except template.TemplateError as err:
            _LOGGER.warning("System prompt template error: %s", err)
            return prompt_template

    def _get_exposed_entities(
        self, options: dict[str, Any]
    ) -> list[dict[str, str]]:
        """Get a list of entities exposed to the conversation agent."""
        max_chars = options.get(CONF_CONTEXT_MAX_CHARS, DEFAULT_CONTEXT_MAX_CHARS)
        entities: list[dict[str, str]] = []
        total_chars = 0

        for state in self.hass.states.async_all():
            # Check if entity is exposed to conversation
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

            # Estimate character usage
            line = f"- {entity_info['entity_id']} ({entity_info['name']}): {entity_info['state']}"
            total_chars += len(line) + 1  # +1 for newline

            if total_chars > max_chars:
                break

            entities.append(entity_info)

        return entities
