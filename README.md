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
uv run horde-oai        # HTTP server only
uv run horde-oai-tui    # TUI (includes embedded HTTP server)
```

The server starts on `http://0.0.0.0:8000` by default. The TUI provides interactive configuration, model browsing, chat, and request logs.

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
  poll_interval: 2.0      # seconds between job status polls (and between impossible-model retries)
  rate_limit_backoff: 5.0 # seconds to freeze after a 429 response
  streaming_retry_delay: 2.0  # seconds between streaming retry attempts

global_min_request_delay: 2.0  # minimum seconds between any two Horde API calls
client_agent: "ai-horde-oai:0.1:github"  # must follow <name>:<version>:<contact> format
```

Environment variable overrides: `HORDE_API_KEY`, `HORDE_API_URL`, `HOST`, `PORT`.

## Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completions (streaming supported) |
| `/v1/completions` | POST | Legacy text completions |
| `/v1/models` | GET | List available model aliases |
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

- **Unavailable Models**: If Horde reports `is_possible=False` (no active workers for the model), the proxy automatically bans that specific model locally for **1 hour** and re-resolves the alias against the remaining model list — explicitly excluding the just-banned model. This retry loop bypasses `max_retries` and continues until a working model is found or the alias is exhausted.
- **Exponential Backoff**: Normal job failures (faults, timeouts) use exponential backoff (`2s, 4s, 8s...` based on `backoff_base`).
- **Streaming Resilience**: Streaming connections track progress; if a job stalls (no queue position change within `stream_stall_timeout` seconds), it is automatically cancelled and retried.
- **Tool Formatting**: Malformed tool-call responses are automatically retried up to 3 times.
- **Global Request Delay**: An absolute minimum delay (default: **2.0s**, `global_min_request_delay`) between any two Horde API calls prevents burst traffic that could trigger rate limits or suspicion.
- **Rate Limit Cooldown**: A 429 response records a local cooldown (`rate_limit_backoff`, default 5 s); all subsequent submissions wait it out transparently before hitting Horde again.
- **IP Block Short-Circuit**: On `rc=TimeoutIP` or `rc=UnsafeIP`, the proxy records a local block (1 h / 6 h respectively) and rejects all further submissions immediately — no Horde calls while blocked.
- **Shared Client State**: The TUI and the FastAPI server share a single `HordeClient` instance, so model bans, 429 cooldowns, and IP blocks are visible and enforced across both.

## Docker

```bash
HORDE_API_KEY=your-key docker compose up --build
```

To use a config file, create `config.yaml` first, then add the volume mount:

```yaml
volumes:
  - ./config.yaml:/root/.ai-horde-oai/config.yaml
```

> **Note:** `docker-compose` (v1) or `podman compose` work as drop-in replacements.

## Development

```bash
uv sync --extra dev
uv run pytest
```
