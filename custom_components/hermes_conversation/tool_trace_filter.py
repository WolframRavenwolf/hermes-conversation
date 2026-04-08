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
    r"execute_code|terminal|process|"
    r"read_file|write_file|patch|search_files|"
    r"browser_[a-z0-9_]+|session_search|memory"
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
_TOOL_TRACE_NAMES = (
    "ha_list_entities",
    "ha_get_state",
    "ha_list_services",
    "ha_call_service",
    "web_search",
    "web_extract",
    "web_crawl",
    "execute_code",
    "terminal",
    "process",
    "read_file",
    "write_file",
    "patch",
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
    "memory",
)
_TOOL_TRACE_HINTS = (
    "ls ",
    "curl ",
    "python ",
    "python3 ",
    "bash ",
    "sh ",
    "jq ",
    "rg ",
    "git ",
    "ha_",
    "ha-call",
    "from hermes_tools import",
    "terminal(",
    "web_search",
    "web_extract",
    "web_crawl",
    "execute_code",
    "| python",
    "| jq",
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

    sanitized_text = _FENCED_CODE_BLOCK_RE.sub(
        lambda match: ""
        if looks_like_tool_trace(match.group(1))
        else match.group(0),
        response_text,
    )
    sanitized_text = _INLINE_CODE_SPAN_RE.sub(
        lambda match: ""
        if looks_like_tool_trace(match.group(1))
        else match.group(0),
        sanitized_text,
    )

    kept_lines: list[str] = []
    for line in sanitized_text.splitlines():
        if looks_like_tool_trace(line):
            continue
        kept_lines.append(line)

    sanitized_text = "\n".join(kept_lines)
    sanitized_text = re.sub(r"\n{3,}", "\n\n", sanitized_text)
    sanitized_text = re.sub(r"[ \t]+\n", "\n", sanitized_text)
    sanitized_text = re.sub(r"\n[ \t]+", "\n", sanitized_text)
    sanitized_text = sanitized_text.strip()

    return sanitized_text or original_text


def looks_like_tool_trace(text: str) -> bool:
    """Heuristically detect tool or shell traces embedded in assistant text."""
    stripped_text = text.strip()
    if not stripped_text:
        return False

    lowered_text = stripped_text.lower()
    normalized_text = lowered_text.strip("` ")
    if stripped_text.startswith(_TOOL_TRACE_LINE_PREFIXES):
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
    return any(hint in lowered_text for hint in _TOOL_TRACE_HINTS)
