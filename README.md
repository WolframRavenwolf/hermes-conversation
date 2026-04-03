# Hermes Conversation

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A [Home Assistant](https://home-assistant.io/) custom integration that connects [Hermes Agent](https://hermes-agent.nousresearch.com/) as a **conversation agent** for voice assistants and the conversation panel.

## Features

- **Conversation agent** — use Hermes Agent as your voice assistant in Home Assistant
- **Streaming** — low latency for voice pipelines (first token arrives fast)
- **Auto-discovery** — automatically detects the [Hermes Agent add-on](https://github.com/WolframRavenwolf/hermes-ha-addon) when running
- **Entity exposure** — includes your smart home device states in the system prompt
- **Multi-turn** — maintains conversation history across turns
- **Configurable** — model, system prompt (Jinja2), temperature, max tokens

## Requirements

- Home Assistant 2024.12 or newer
- A running [Hermes Agent](https://github.com/NousResearch/hermes-agent) instance with the API server enabled:
  - **Easiest:** Install the [Hermes Agent add-on](https://github.com/WolframRavenwolf/hermes-ha-addon) and enable the API in the add-on configuration
  - **Alternative:** Run Hermes Agent standalone and point the integration at its API endpoint

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots in the top right → **Custom repositories**
3. Add `https://github.com/WolframRavenwolf/hermes-conversation` as an **Integration**
4. Search for "Hermes Conversation" and install it
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/hermes_conversation` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

### With the Hermes Agent Add-on (Auto-Discovery)

1. Make sure the Hermes Agent add-on is running with **Enable API** turned on
2. Go to **Settings → Devices & Services → Add Integration**
3. Search for "Hermes Conversation"
4. The integration will auto-detect the add-on — click **Submit** to confirm

### Manual Setup (Standalone Hermes Agent)

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "Hermes Conversation"
3. Enter the **Host**, **Port**, and optionally the **API Key** of your Hermes Agent instance
4. Click **Submit**

### Using as Voice Assistant

1. Go to **Settings → Voice Assistants**
2. Create a new assistant or edit an existing one
3. Select **Hermes Conversation** as the **Conversation agent**

### Options

After setup, configure the integration via **Settings → Devices & Services → Hermes Conversation → Configure**:

| Option | Default | Description |
|--------|---------|-------------|
| Model | `hermes-agent` | Model name sent to the API |
| System Prompt | (built-in) | Jinja2 template for the system prompt |
| Temperature | 0.7 | LLM temperature (0.0–2.0) |
| Max Tokens | 4096 | Maximum response tokens |
| Include exposed entities | Yes | Include smart home device states in the prompt |
| Max context characters | 12000 | Character limit for the entity context block |

## How It Works

This integration communicates with Hermes Agent's OpenAI-compatible API (`/v1/chat/completions`) using only Home Assistant's built-in HTTP client — **no external Python dependencies**.

Hermes Agent handles tool execution (controlling lights, checking sensors, etc.) server-side through its own Home Assistant integration. This means the conversation integration stays simple: it sends your message, gets back the response (which may include results from tool actions the agent performed), and displays it.

## License

[MIT](LICENSE)
