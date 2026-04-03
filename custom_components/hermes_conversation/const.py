"""Constants for the Hermes Conversation integration."""

DOMAIN = "hermes_conversation"

# ---------------------------------------------------------------------------
# Addon discovery
# ---------------------------------------------------------------------------
ADDON_SLUG_SUFFIX = "hermes_agent"
ADDON_INTERNAL_PORT = 8080

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
CONF_MODEL = "model"
CONF_PROMPT = "prompt"
CONF_TEMPERATURE = "temperature"
CONF_MAX_TOKENS = "max_tokens"
CONF_INCLUDE_EXPOSED_ENTITIES = "include_exposed_entities"
CONF_CONTEXT_MAX_CHARS = "context_max_chars"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_HOST = "homeassistant.local"
DEFAULT_PORT = 8443
DEFAULT_MODEL = "hermes-agent"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 4096
DEFAULT_CONTEXT_MAX_CHARS = 12000
DEFAULT_INCLUDE_EXPOSED_ENTITIES = True
DEFAULT_TIMEOUT = 120
DEFAULT_STREAM_TIMEOUT = 300
DEFAULT_MAX_HISTORY_MESSAGES = 100

DEFAULT_PROMPT = (
    "You are in a voice chat with the user via the Home Assistant app.\n"
    "The current time is {{ now().strftime('%H:%M') }}.\n"
    "Today's date is {{ now().strftime('%Y-%m-%d') }}.\n"
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
