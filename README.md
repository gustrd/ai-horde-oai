# ai-horde-oai

OpenAI-compatible API proxy for [AI Horde](https://aihorde.net/).

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended package manager)

## Setup

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

## Usage

```bash
uv run horde-oai
```

The server starts on `http://0.0.0.0:8000` by default. A Textual TUI is available for interactive configuration, model browsing, chat, and request logs.

## Configuration

Create `~/.ai-horde-oai/config.yaml`:

```yaml
horde_api_key: "your-key-here"   # get one at https://aihorde.net/register
horde_api_url: "https://aihorde.net/api"
host: "0.0.0.0"
port: 8000

default_model: "best"
model_aliases:
  large: "aphrodite/llama-3.1-70b-instruct"
  creative: "koboldcpp/mistral-nemo-12b"

model_min_context: 4096
model_blocklist: ["yi"]

max_concurrent_requests: 3   # max simultaneous Horde jobs; 0 = unlimited

retry:
  max_retries: 2
  timeout_seconds: 300
```

Environment variable overrides: `HORDE_API_KEY`, `HORDE_API_URL`, `HOST`, `PORT`.

## Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completions (streaming supported) |
| `/v1/completions` | POST | Legacy text completions |
| `/v1/models` | GET | List available model aliases |
| `/v1/images/generations` | POST | DALL-E compatible image generation |
| `/health` | GET | Health check |

## Model Aliases

Clients use short model names — real Horde model names never leave the server.

| Alias | Behavior |
|---|---|
| `default` | Uses `default_model` from config |
| `best` | Auto-picks model with most workers |
| `fast` | Auto-picks model with shortest queue |
| Custom aliases | Defined in `model_aliases` in config |

## Tool / Function Calling

OpenAI-style `tools` and `tool_choice` are supported. Tools are injected into the prompt using the model's native format (Hermes `<tool_call>` or Llama-3 JSON) and parsed back into an OpenAI `tool_calls` response. Streaming is supported. Malformed tool call responses are automatically retried up to 3 times.

## Concurrency

`max_concurrent_requests` limits how many Horde jobs are in flight at once (default: 3). This applies to all generation endpoints. Set to `0` to disable the limit. Requires a server restart to take effect.

## Retry & Reliability

The proxy implements sophisticated retry logic to ensure high reliability despite Horde's distributed nature:

- **Unavailable Models**: If Horde reports `is_possible=False` (no active workers for the model), the proxy automatically bans that specific model locally for **1 hour**, enforcing a 2-second delay, and automatically fallbacks to the next best model for the requested alias. This retry loop **bypasses standard retry limits** and continues until a suitable model is found and successfully finishes a job, or until all models for that alias have been exhausted.
- **Exponential Backoff**: Normal job failures (faults, timeouts) use exponential backoff (`2s, 4s, 8s...`).
- **Streaming Resilience**: Streaming connections track progress; if a job stalls (no queue change), it is automatically cancelled and retried.
- **Tool Formatting**: Malformed tool-call responses are automatically retried up to 3 times.
- **Global Request Delay**: To prevent account flags due to concurrent burst traffic, the proxy enforces an absolute minimum delay (default: **2.0s**) between any two hits to the Horde API across all clients and models.

## Docker

```bash
docker-compose up
```

Set `HORDE_API_KEY` in the environment or mount a `config.yaml`.

## Development

```bash
uv run pytest
```
