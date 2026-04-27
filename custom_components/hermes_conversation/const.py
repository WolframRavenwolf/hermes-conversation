"""Constants for the Hermes Conversation integration."""

DOMAIN = "hermes_conversation"

# ---------------------------------------------------------------------------
# Config entry keys (stored at setup time)
# ---------------------------------------------------------------------------
CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_KEY = "api_key"
CONF_USE_SSL = "use_ssl"
CONF_VERIFY_SSL = "verify_ssl"

# ---------------------------------------------------------------------------
# Options keys (user-changeable after setup)
# ---------------------------------------------------------------------------
CONF_PROMPT = "prompt"
CONF_INCLUDE_EXPOSED_ENTITIES = "include_exposed_entities"
CONF_CONTEXT_MAX_CHARS = "context_max_chars"
CONF_HIDE_TOOL_TRACES = "hide_tool_traces"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_HOST = "homeassistant.local"
DEFAULT_PORT = 8443
DEFAULT_CONTEXT_MAX_CHARS = 12000
DEFAULT_INCLUDE_EXPOSED_ENTITIES = False
DEFAULT_HIDE_TOOL_TRACES = False
DEFAULT_TIMEOUT = 120
DEFAULT_STREAM_TIMEOUT = 300
DEFAULT_MAX_HISTORY_MESSAGES = 100

DEFAULT_PROMPT = (
    "You are in a voice chat with {{ user_name }} via the Home Assistant app.\n"
    "Current date and time: {{ now().strftime('%Y-%m-%d %H:%M %Z') }}.\n"
    "{% if ha_name %}The home is called {{ ha_name }}.{% endif %}\n"
    "{% if exposed_entities %}\n"
    "Available devices:\n"
    "{% for entity in exposed_entities %}"
    "- {{ entity.entity_id }} ({{ entity.name }}): {{ entity.state }}\n"
    "{% endfor %}"
    "{% endif %}\n"
    "Answer in the user's language. Be concise for voice responses."
)

# ---------------------------------------------------------------------------
# API paths
# ---------------------------------------------------------------------------
API_CHAT_COMPLETIONS = "/v1/chat/completions"
API_MODELS = "/v1/models"
API_HEALTH = "/health"
