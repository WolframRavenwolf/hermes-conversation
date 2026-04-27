"""Conversation entity for Hermes Agent."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

from homeassistant.components import conversation as ha_conversation
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
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

try:
    from homeassistant.components.conversation.chat_log import (
        ChatLog,
        async_get_chat_log,
    )
    from homeassistant.helpers.chat_session import async_get_chat_session
except ImportError:  # pragma: no cover - compatibility with older Home Assistant
    ChatLog = Any  # type: ignore[misc,assignment]
    async_get_chat_log = None
    async_get_chat_session = None

from .api import HermesApiClient, HermesApiError
from .const import (
    CONF_CONTEXT_MAX_CHARS,
    CONF_INCLUDE_EXPOSED_ENTITIES,
    CONF_PROMPT,
    DEFAULT_CONTEXT_MAX_CHARS,
    DEFAULT_INCLUDE_EXPOSED_ENTITIES,
    DEFAULT_MAX_HISTORY_MESSAGES,
    DEFAULT_PROMPT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
_MAX_CACHED_CONVERSATIONS = 50
_HAS_CHAT_LOG_API = (
    async_get_chat_log is not None and async_get_chat_session is not None
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Hermes conversation entity."""
    client: HermesApiClient = hass.data[DOMAIN][entry.entry_id]["client"]
    async_add_entities([HermesConversationEntity(hass, entry, client)])


class HermesConversationEntity(
    ha_conversation.ConversationEntity,
    AbstractConversationAgent,
):
    """Hermes Agent conversation entity for Home Assistant."""

    _attr_supports_streaming = True

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
        self._attr_unique_id = entry.entry_id
        self._attr_name = entry.title or "Hermes Agent"
        self._attr_supported_features = (
            ha_conversation.ConversationEntityFeature.CONTROL
        )
        # conversation_id -> list of {"role": ..., "content": ...}
        self._history: OrderedDict[str, list[dict[str, str]]] = OrderedDict()

    @property
    def supported_languages(self) -> list[str] | str:
        """Return supported languages (all — the LLM handles it)."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register the entity as both conversation entity and legacy agent."""
        await super().async_added_to_hass()
        ha_conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the legacy agent alias when removing the entity."""
        ha_conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def async_process(
        self, user_input: ConversationInput
    ) -> ConversationResult:
        """Process a conversation turn."""
        try:
            if _HAS_CHAT_LOG_API:
                return await self._async_process_modern(user_input)
            return await self._async_process_legacy(user_input)
        except Exception:
            _LOGGER.exception("Unexpected error in async_process")
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                "An internal error occurred. Check the logs.",
            )
            return self._build_conversation_result(
                intent_response,
                user_input.conversation_id or "default",
            )

    async def _async_process_modern(
        self, user_input: ConversationInput
    ) -> ConversationResult:
        """Process a conversation turn using Home Assistant chat sessions."""
        assert async_get_chat_log is not None
        assert async_get_chat_session is not None

        with (
            async_get_chat_session(self.hass, user_input.conversation_id) as session,
            async_get_chat_log(self.hass, session, user_input) as chat_log,
        ):
            return await self._async_handle_message(user_input, chat_log)

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Handle a conversation turn using the modern chat log API."""
        options = self.entry.options
        user_name = await self._get_user_name(user_input)
        system_prompt = self._build_system_prompt(options, user_input, user_name)
        messages = self._build_messages_from_chat_log(chat_log, system_prompt)

        response_text = ""

        try:
            async for content in chat_log.async_add_delta_content_stream(
                self.entity_id or self.entry.entry_id,
                self._async_stream_assistant_response(messages),
            ):
                if (
                    getattr(content, "role", None) == "assistant"
                    and getattr(content, "content", None) is not None
                ):
                    response_text += content.content
        except HermesApiError as err:
            _LOGGER.error("Hermes API error: %s", err)
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Error communicating with Hermes Agent: {err}",
            )
            return self._build_conversation_result(
                intent_response,
                chat_log.conversation_id,
            )

        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(response_text)

        return self._build_conversation_result(
            intent_response,
            chat_log.conversation_id,
        )

    async def _async_process_legacy(
        self, user_input: ConversationInput
    ) -> ConversationResult:
        """Process a conversation turn on older Home Assistant versions."""
        options = self.entry.options
        user_name = await self._get_user_name(user_input)
        system_prompt = self._build_system_prompt(options, user_input, user_name)

        conv_id = user_input.conversation_id or "default"
        history = self._history.setdefault(conv_id, [])

        messages = self._build_messages_from_history(history, system_prompt)
        messages.append({"role": "user", "content": user_input.text})

        try:
            response_text = await self._get_response(messages)
        except HermesApiError as err:
            _LOGGER.error("Hermes API error: %s", err)
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Error communicating with Hermes Agent: {err}",
            )
            return self._build_conversation_result(
                intent_response,
                conv_id,
            )

        history.append({"role": "user", "content": user_input.text})
        history.append({"role": "assistant", "content": response_text})
        self._trim_legacy_history(history)
        self._trim_cached_conversations()

        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(response_text)

        return self._build_conversation_result(intent_response, conv_id)

    async def _async_stream_assistant_response(
        self,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[dict[str, str]]:
        """Yield assistant deltas for Home Assistant's chat log."""
        sent_role = False
        received_delta = False

        try:
            async for delta in self.client.async_stream_message(messages):
                if not delta:
                    continue
                if not sent_role:
                    yield {"role": "assistant"}
                    sent_role = True
                received_delta = True
                yield {"content": delta}
        except HermesApiError as err:
            if received_delta:
                _LOGGER.warning(
                    "Streaming interrupted after partial response, keeping partial text: %s",
                    err,
                )
                return

            _LOGGER.debug(
                "Streaming failed before first token, falling back to non-streaming: %s",
                err,
            )

        if received_delta:
            return

        response_text = await self.client.async_send_message(messages)
        if response_text:
            if not sent_role:
                yield {"role": "assistant"}
            yield {"content": response_text}

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

    def _build_messages_from_chat_log(
        self,
        chat_log: ChatLog,
        system_prompt: str,
    ) -> list[dict[str, str]]:
        """Build Hermes messages from a Home Assistant chat log."""
        history_messages: list[dict[str, str]] = []

        for content in chat_log.content:
            role = getattr(content, "role", None)
            message_text = getattr(content, "content", None)

            # The current system prompt is rebuilt for each turn, so we avoid
            # replaying older system entries from Home Assistant's chat log.
            if role not in ("user", "assistant") or not message_text:
                continue

            history_messages.append({"role": role, "content": message_text})

        return self._build_messages_from_history(history_messages, system_prompt)

    def _build_messages_from_history(
        self,
        history: list[dict[str, str]],
        system_prompt: str,
    ) -> list[dict[str, str]]:
        """Build Hermes messages from text history."""
        messages: list[dict[str, str]] = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if history:
            messages.extend(history[-DEFAULT_MAX_HISTORY_MESSAGES:])

        return messages

    def _trim_legacy_history(self, history: list[dict[str, str]]) -> None:
        """Trim legacy in-memory history."""
        while len(history) > DEFAULT_MAX_HISTORY_MESSAGES:
            history.pop(0)
            if history and history[0]["role"] == "assistant":
                history.pop(0)

    def _trim_cached_conversations(self) -> None:
        """Evict oldest legacy conversations if too many are cached."""
        while len(self._history) > _MAX_CACHED_CONVERSATIONS:
            self._history.popitem(last=False)

    def _build_system_prompt(
        self,
        options: dict[str, Any],
        user_input: ConversationInput,
        user_name: str,
    ) -> str:
        """Build the system prompt, including pipeline-provided extra instructions."""
        system_prompt = self._render_system_prompt(options, user_name)

        extra_system_prompt = getattr(user_input, "extra_system_prompt", None)
        if extra_system_prompt:
            return (
                f"{system_prompt}\n\n{extra_system_prompt}"
                if system_prompt
                else extra_system_prompt
            )

        return system_prompt

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
            return ""

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

        return rendered_prompt

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

    def _build_conversation_result(
        self,
        intent_response: intent.IntentResponse,
        conversation_id: str,
    ) -> ConversationResult:
        """Build a conversation result."""
        return ConversationResult(
            response=intent_response,
            conversation_id=conversation_id,
        )
