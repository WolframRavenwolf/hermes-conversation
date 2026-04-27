"""Helpers for hiding internal tool traces from user-facing responses."""

from __future__ import annotations

import re
from typing import Any

from .const import CONF_HIDE_TOOL_TRACES, DEFAULT_HIDE_TOOL_TRACES

_TOOL_TRACE_PROMPT = (
    "Do not expose internal tool usage, shell commands, code execution traces, "
    "or research steps in the user-facing answer. Only provide the final answer."
)
_FENCED_CODE_BLOCK_RE = re.compile(r"```(?:[^\n`]*\n)?(.*?)```", re.DOTALL)
_INLINE_CODE_SPAN_RE = re.compile(r"`([^`]*)`", re.DOTALL)
_LEADING_TOOL_LABEL_RE = re.compile(
    r"^(?:[^\w\s`]+\s*)?(?:"
    r"ha_[a-z0-9_]+|"
    r"web_search|web_extract|web_crawl|"
    r"execute_code|"
    r"read_file|write_file|search_files|"
    r"browser_[a-z0-9_]+|session_search"
    r")(?:\b|$)",
    re.IGNORECASE,
)
_TOOL_TRACE_LINE_PREFIXES = (
    "┊",
    "🏠",
    "💻",
    "⚙️",
    "📖",
    "✍️",
    "🔧",
    "🔎",
    "🔍",
    "🌐",
    "📄",
    "🕸️",
    "📸",
    "👆",
    "⌨️",
    "◀️",
    "🖼️",
    "👁️",
    "📋",
    "🧠",
    "📚",
    "🎨",
    "🔊",
    "📨",
    "⏰",
    "🧪",
    "🐍",
    "🔀",
    "⚡",
)
_EMOJI_VARIATION_SELECTOR = "\ufe0f"
_NORMALIZED_TOOL_TRACE_LINE_PREFIXES = tuple(
    prefix.replace(_EMOJI_VARIATION_SELECTOR, "") for prefix in _TOOL_TRACE_LINE_PREFIXES
)
_TOOL_TRACE_NAMES = (
    "ha_list_entities",
    "ha_get_state",
    "ha_list_services",
    "ha_call_service",
    "web_search",
    "web_extract",
    "web_crawl",
    "execute_code",
    "read_file",
    "write_file",
    "search_files",
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_scroll",
    "browser_back",
    "browser_press",
    "browser_get_images",
    "browser_vision",
    "session_search",
)
_CODE_TRACE_PATTERNS = (
    re.compile(r"\bfrom hermes_tools import\b", re.IGNORECASE),
    re.compile(r"\bterminal\(", re.IGNORECASE),
    re.compile(r"\b(?:web_search|web_extract|web_crawl|execute_code)\b", re.IGNORECASE),
    re.compile(r"^(?:\$+\s*)?(?:curl|python3?|bash|sh|jq|rg|git|ls)\b", re.IGNORECASE),
    re.compile(r"\|\s*(?:python3?|jq|grep)\b", re.IGNORECASE),
)


def should_hide_tool_traces(options: dict[str, Any]) -> bool:
    """Return whether tool traces should be removed from user-facing output."""
    return options.get(CONF_HIDE_TOOL_TRACES, DEFAULT_HIDE_TOOL_TRACES)


def append_tool_trace_prompt(options: dict[str, Any], system_prompt: str) -> str:
    """Append guidance to suppress tool traces in final answers."""
    if not should_hide_tool_traces(options):
        return system_prompt

    if system_prompt:
        return f"{system_prompt}\n\n{_TOOL_TRACE_PROMPT}"

    return _TOOL_TRACE_PROMPT


def sanitize_response_text(response_text: str) -> str:
    """Remove obvious tool execution traces from a user-facing response."""
    original_text = response_text.strip()
    if not original_text:
        return response_text

    preserved_fenced_blocks: dict[str, str] = {}

    def _replace_fenced_block(match: re.Match[str]) -> str:
        block_text = match.group(0)
        block_body = match.group(1)
        if looks_like_tool_trace(block_body, from_code=True):
            return ""

        placeholder = f"__HERMES_FENCED_BLOCK_{len(preserved_fenced_blocks)}__"
        preserved_fenced_blocks[placeholder] = block_text
        return placeholder

    sanitized_text = _FENCED_CODE_BLOCK_RE.sub(_replace_fenced_block, response_text)
    sanitized_text = _INLINE_CODE_SPAN_RE.sub(
        lambda match: ""
        if looks_like_tool_trace(match.group(1), from_code=True)
        else match.group(0),
        sanitized_text,
    )

    for placeholder, block_text in preserved_fenced_blocks.items():
        sanitized_text = sanitized_text.replace(placeholder, block_text)

    kept_lines: list[str] = []
    for line in sanitized_text.splitlines():
        if looks_like_tool_trace(line):
            continue
        kept_lines.append(line)

    sanitized_text = "\n".join(kept_lines)
    sanitized_text = re.sub(r"\n{3,}", "\n\n", sanitized_text)
    sanitized_text = re.sub(r"[ \t]+\n", "\n", sanitized_text)
    sanitized_text = sanitized_text.strip()

    return sanitized_text or original_text


def looks_like_tool_trace(text: str, *, from_code: bool = False) -> bool:
    """Heuristically detect tool or shell traces embedded in assistant text."""
    stripped_text = text.strip()
    if not stripped_text:
        return False

    lowered_text = stripped_text.lower()
    normalized_text = lowered_text.strip("` ")
    normalized_prefix_text = stripped_text.replace(_EMOJI_VARIATION_SELECTOR, "")
    if stripped_text.startswith(_TOOL_TRACE_LINE_PREFIXES):
        return True
    if normalized_prefix_text.startswith(_NORMALIZED_TOOL_TRACE_LINE_PREFIXES):
        return True
    if stripped_text.startswith("$ "):
        return True
    if any(
        normalized_text == tool_name
        or normalized_text.startswith(f"{tool_name} ")
        or normalized_text.startswith(f"{tool_name}...")
        for tool_name in _TOOL_TRACE_NAMES
    ):
        return True
    if _LEADING_TOOL_LABEL_RE.match(stripped_text):
        return True
    if from_code:
        return any(pattern.search(stripped_text) for pattern in _CODE_TRACE_PATTERNS)
    return False
