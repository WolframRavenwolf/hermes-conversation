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
    CONF_AUTO_FOLLOW_UP,
    CONF_CONTEXT_MAX_CHARS,
    CONF_INCLUDE_EXPOSED_ENTITIES,
    CONF_PROMPT,
    DEFAULT_AUTO_FOLLOW_UP,
    DEFAULT_CONTEXT_MAX_CHARS,
    DEFAULT_INCLUDE_EXPOSED_ENTITIES,
    DEFAULT_MAX_HISTORY_MESSAGES,
    DEFAULT_PROMPT,
)

_LOGGER = logging.getLogger(__name__)
_QUESTION_ENDINGS = ("?", "？")
_INLINE_QUESTION_ENDINGS = ("?", "？")
_TRAILING_FOLLOW_UP_MAX_CHARS = 120
_TRAILING_FOLLOW_UP_MAX_WORDS = 20
_TRAILING_FOLLOW_UP_MAX_SENTENCE_ENDERS = 1
_TRAILING_CLOSERS = "\"'”’)]}»"


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
            return self._build_conversation_result(
                intent_response,
                user_input.conversation_id or "default",
            )

    async def _async_process_inner(
        self, user_input: ConversationInput
    ) -> ConversationResult:
        """Inner processing — wrapped by async_process for error logging."""
        options = self.entry.options

        # Resolve username from HA auth
        user_name = await self._get_user_name(user_input)

        # Build system prompt (optional — Hermes Agent has its own)
        system_prompt = self._render_system_prompt(options, user_name)

        # Append extra system prompt from HA voice pipeline if present
        extra = getattr(user_input, "extra_system_prompt", None)
        if extra:
            system_prompt = (system_prompt + "\n\n" + extra) if system_prompt else extra

        # Get or create conversation history
        conv_id = user_input.conversation_id or "default"
        history = self._history.setdefault(conv_id, [])

        # Build messages: system (if any) + history + new user message
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": user_input.text})

        # Call the API — try streaming first, fall back to non-streaming
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
        continue_conversation = self._should_continue_conversation(
            options, response_text
        )

        return self._build_conversation_result(
            intent_response,
            conv_id,
            continue_conversation=continue_conversation,
        )

    async def _get_response(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        """Get a response from the API, trying streaming first."""
        # Try streaming for lower TTFB
        try:
            chunks: list[str] = []
            async for delta in self.client.async_stream_message(messages):
                chunks.append(delta)

            if chunks:
                return "".join(chunks)
        except HermesApiError:
            _LOGGER.debug("Streaming failed, falling back to non-streaming")

        # Fall back to non-streaming
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
            return ""

        # Build template variables
        variables: dict[str, Any] = {
            "ha_name": self.hass.config.location_name,
            "user_name": user_name,
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

    def _should_continue_conversation(
        self, options: dict[str, Any], response_text: str
    ) -> bool:
        """Return whether the voice pipeline should keep listening."""
        if not options.get(CONF_AUTO_FOLLOW_UP, DEFAULT_AUTO_FOLLOW_UP):
            return False

        stripped_text = response_text.strip().rstrip(_TRAILING_CLOSERS)
        if not stripped_text:
            return False

        if stripped_text.endswith(_QUESTION_ENDINGS):
            return True

        last_question_pos = max(
            stripped_text.rfind(marker) for marker in _INLINE_QUESTION_ENDINGS
        )
        if last_question_pos == -1:
            return False

        trailing_text = stripped_text[last_question_pos + 1 :].strip()
        if not trailing_text:
            return True

        trailing_words = trailing_text.split()
        trailing_sentence_enders = sum(
            trailing_text.count(marker) for marker in ".!?！？;；"
        )

        return (
            len(trailing_text) <= _TRAILING_FOLLOW_UP_MAX_CHARS
            and len(trailing_words) <= _TRAILING_FOLLOW_UP_MAX_WORDS
            and trailing_sentence_enders <= _TRAILING_FOLLOW_UP_MAX_SENTENCE_ENDERS
        )

    def _build_conversation_result(
        self,
        intent_response: intent.IntentResponse,
        conversation_id: str,
        *,
        continue_conversation: bool = False,
    ) -> ConversationResult:
        """Build a conversation result, preserving compatibility with older HA."""
        result_kwargs: dict[str, Any] = {
            "response": intent_response,
            "conversation_id": conversation_id,
        }

        if "continue_conversation" in getattr(
            ConversationResult, "__dataclass_fields__", {}
        ):
            result_kwargs["continue_conversation"] = continue_conversation

        return ConversationResult(**result_kwargs)
