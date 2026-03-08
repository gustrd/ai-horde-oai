# ai-horde-oai

OpenAI-compatible API proxy for [AI Horde](https://aihorde.net/).

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended package manager)

## Setup

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install the project (with dev dependencies)
uv sync --extra dev

# Or install just runtime deps
uv sync
```

## Usage

```bash
# Start the server
uv run horde-oai

# Or directly
uv run python -m app.main
```

The server starts on `http://0.0.0.0:8000` by default.

## Configuration

Copy or create `~/.ai-horde-oai/config.yaml`:

```yaml
horde_api_key: "your-key-here"   # get one at https://aihorde.net/register
horde_api_url: "https://aihorde.net/api"
host: "0.0.0.0"
port: 8000

default_model: "aphrodite/llama-3.1-8b-instruct"
model_aliases:
  large: "aphrodite/llama-3.1-70b-instruct"
  creative: "koboldcpp/mistral-nemo-12b"

model_min_context: 4096
model_blocklist: ["yi"]

retry:
  max_retries: 2
  timeout_seconds: 300
  broaden_on_retry: true
```

Environment variable overrides: `HORDE_API_KEY`, `HORDE_API_URL`, `HOST`, `PORT`.

## Development

```bash
# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=app

# Run a single test file
uv run pytest tests/test_filters.py -v
```

## Docker

```bash
docker-compose up
```

Set `HORDE_API_KEY` in the environment or mount a `config.yaml`.

## Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completions (streaming supported) |
| `/v1/completions` | POST | Legacy text completions |
| `/v1/models` | GET | List available model aliases |
| `/v1/models/{id}` | GET | Single model info |
| `/v1/images/generations` | POST | DALL-E compatible image generation |
| `/health` | GET | Health check |

## Model Aliases

Clients use **dummy model names** — the real Horde model names never leave the server.

| Alias | Behavior |
|---|---|
| `default` | Uses `default_model` from config |
| `best` | Auto-picks model with most workers |
| `fast` | Auto-picks model with shortest queue |
| Custom aliases | Configured in `model_aliases` in config |
