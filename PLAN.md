# ai-horde-oai — Project Plan

OpenAI-compatible API proxy that forwards requests to [AI Horde](https://aihorde.net/).
Inspired by [horde-openai-proxy](https://github.com/Haidra-Org/horde-openai-proxy) but built as a standalone server.

## Goals

- Drop-in replacement for OpenAI API in tools like SillyTavern, Open WebUI, etc.
- Translate OpenAI Chat Completions and Image Generation requests to AI Horde
- Support streaming (SSE) responses
- Server-side API key — clients don't need a Horde key
- Dummy model names exposed to clients, mapped to real Horde models server-side
- Smart model routing (`best` mode)
- TUI for interactive config, testing, and monitoring

## Architecture

```
Client (OpenAI SDK / curl / app)
  │  uses dummy model names (e.g. "default", "best")
  ▼
┌──────────────────────────────────────┐
│  FastAPI Server                      │
│  (OpenAI-compatible API)             │
│  - Accepts dummy model names         │
│  - Maps to real Horde model(s)       │
│  - Server-side Horde API key         │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  Horde Client Layer                  │
│  - Submit async job                  │
│  - Poll for completion              │
│  - Auto-retry on failure            │
│  - Queue position / ETA tracking    │
└──────────────┬───────────────────────┘
               │
               ▼
         AI Horde API
         (aihorde.net)
```

## Model Name Mapping

Clients see **dummy model names** via `/v1/models`. The proxy maps them to real Horde models.

| Dummy name (client sees) | Behavior |
|---|---|
| `best` | Auto-pick the best available model (most workers, lowest queue) |
| `default` | Uses `default_model` from config |
| `fast` | Pick model with shortest estimated queue time |
| Custom aliases | Configured in `config.yaml` model_aliases section |

The `/v1/models` endpoint returns these dummy names. Clients never see or need to know
the real Horde model names. The mapping is configured server-side.

```yaml
# config.yaml example
model_aliases:
  default: "aphrodite/llama-3.1-8b-instruct"
  large: "aphrodite/llama-3.1-70b-instruct"
  creative: "koboldcpp/mistral-nemo-12b"
```

Built-in aliases (`best`, `fast`, `default`) always exist. Custom aliases are additive.

## Endpoints to Implement

### Phase 1 — Core (Text)

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completions (main endpoint) |
| `/v1/models` | GET | List dummy model names |
| `/v1/models/{model}` | GET | Single model info |

### Phase 2 — Extras (Text)

| Endpoint | Method | Description |
|---|---|---|
| `/v1/completions` | POST | Legacy text completions |
| `/v1/chat/completions` (stream) | POST | SSE streaming via polling |

### Phase 3 — Image Generation

| Endpoint | Method | Description |
|---|---|---|
| `/v1/images/generations` | POST | DALL-E compatible image generation |

Maps to Horde's `POST /v2/generate/async` (image mode). Returns base64 or URL.

## AI Horde API Flow

### Text Generation
1. **Submit**: `POST /v2/generate/text/async` — send prompt + params, get back a job `id`
2. **Poll**: `GET /v2/generate/text/status/{id}` — check `done` flag, get `generations` array
3. **Cancel**: `DELETE /v2/generate/text/status/{id}` — cancel if client disconnects

### Image Generation
1. **Submit**: `POST /v2/generate/async` — send prompt + image params, get back a job `id`
2. **Poll**: `GET /v2/generate/status/{id}` — check `done` flag, get image URLs
3. **Cancel**: `DELETE /v2/generate/status/{id}`

## Request Translation

### OpenAI → Horde (Text)

| OpenAI field | Horde equivalent |
|---|---|
| `model` | Resolved via alias mapping → `models[]` |
| `messages` | Rendered into `prompt` using chat template |
| `max_tokens` | `params.max_length` |
| `temperature` | `params.temperature` |
| `top_p` | `params.top_p` |
| `stop` | `params.stop_sequence` |
| `n` | `params.n` |
| `stream` | Handled locally (poll + SSE) |

### OpenAI → Horde (Images)

| OpenAI field | Horde equivalent |
|---|---|
| `prompt` | `prompt` |
| `model` | Resolved via alias → `models[]` |
| `size` | `params.width` + `params.height` |
| `n` | `params.n` |
| `quality` | `params.steps` (mapped: standard=30, hd=50) |
| `response_format` | `r2` (url) or inline (base64) |

### Horde → OpenAI Response

| Horde field | OpenAI equivalent |
|---|---|
| `generations[].text` | `choices[].message.content` |
| `generations[].model` | `model` (mapped back to dummy name) |
| `generations[].worker_name` | Ignored (or metadata) |
| `kudos` | `usage.total_tokens` (approximate) |

## Chat Template Handling

Messages (`system`, `user`, `assistant`) need to be rendered into a single prompt string
using the model's chat template. Options:

- **Option A**: Use `transformers` / `jinja2` to apply HuggingFace chat templates (like horde-openai-proxy does)
- **Option B**: Use a built-in set of common templates (ChatML, Llama, Alpaca, Mistral)
- **Option C**: Send messages as-is in ChatML format and let the Horde worker handle it

**Decision**: Start with Option C (ChatML default), add Option B for known model families.

## Model Filtering

Filter which Horde models are eligible for alias mapping and `best`/`fast` selection.
Filters are applied server-side.

| Filter | Config key | Description |
|---|---|---|
| Min context length | `model_min_context` | Hide models with context window below this value (e.g. `4096`) |
| Min max output length | `model_min_max_length` | Hide models that can't generate at least N tokens |
| Name whitelist | `model_whitelist` | List of substrings; only models matching **any** substring are eligible (e.g. `[llama, mistral]`) |
| Name blocklist | `model_blocklist` | List of substrings; models matching **any** substring are hidden (e.g. `[yi, phi]`) |

**Evaluation order**: whitelist → blocklist → min context → min max length.

## Worker Filtering

Control which Horde workers can serve requests.

| Filter | Config key | Description |
|---|---|---|
| Trusted only | `trusted_workers` | Only use trusted workers (`true`/`false`) |
| Worker whitelist | `worker_whitelist` | List of worker IDs or name substrings to allow |
| Worker blocklist | `worker_blocklist` | List of worker IDs or name substrings to reject |

Worker filters are sent in the Horde request body (`workers[]`, `trusted_workers`).
Blocklist uses Horde's `worker_blacklist` field.

## Auto-Retry

If a generation job fails or times out, the proxy retries automatically:

- **Max retries**: configurable (default: `2`)
- **Retry on**: job failure, timeout, empty response
- **Backoff**: on retry, optionally broaden the model/worker pool (e.g. drop worker whitelist, try next-best model)
- **Timeout**: configurable per-request timeout (default: `300s`). If the Horde job hasn't completed within this window, cancel and retry.
- Client receives an error only after all retries are exhausted

```yaml
retry:
  max_retries: 2
  timeout_seconds: 300
  broaden_on_retry: true  # relax filters on subsequent attempts
```

## Queue Position & ETA

While polling a Horde job, the proxy can expose estimated wait time:

- Horde status response includes `queue_position`, `wait_time`
- **Streaming mode**: send SSE comments with queue updates before the actual response: `": queue_position=3, eta=12s"`
- **Non-streaming mode**: the client just waits, but the TUI and request logs show live queue position
- **TUI dashboard**: shows active requests with their queue positions

## Configuration

All config lives in `~/.ai-horde-oai/config.yaml`. Env vars override config file values.

```yaml
# ~/.ai-horde-oai/config.yaml

horde_api_key: "your-key-here"        # server-side, not exposed to clients
horde_api_url: "https://aihorde.net/api"
host: "0.0.0.0"
port: 8000
client_agent: "ai-horde-oai:0.1:github"

# Model aliases (dummy names clients use)
default_model: "aphrodite/llama-3.1-8b-instruct"
model_aliases:
  large: "aphrodite/llama-3.1-70b-instruct"
  creative: "koboldcpp/mistral-nemo-12b"

# Model filters
model_min_context: 4096
model_min_max_length: 0
model_whitelist: []          # empty = allow all
model_blocklist: ["yi"]

# Worker filters
trusted_workers: false
worker_whitelist: []
worker_blocklist: []

# Retry
retry:
  max_retries: 2
  timeout_seconds: 300
  broaden_on_retry: true

# Image generation defaults
image_defaults:
  model: "stable_diffusion_xl"
  steps: 30
  cfg_scale: 7.5
  width: 1024
  height: 1024
```

| Env var override | Config key |
|---|---|
| `HORDE_API_KEY` | `horde_api_key` |
| `HORDE_API_URL` | `horde_api_url` |
| `HOST` | `host` |
| `PORT` | `port` |

## Project Structure

```
ai-horde-oai/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yaml
├── README.md
├── PLAN.md
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, lifespan, config
│   ├── config.py            # Settings (config.yaml + env vars)
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── chat.py           # /v1/chat/completions
│   │   ├── completions.py    # /v1/completions
│   │   ├── models.py         # /v1/models (dummy names)
│   │   └── images.py         # /v1/images/generations
│   ├── horde/
│   │   ├── __init__.py
│   │   ├── client.py         # HTTP client for AI Horde API
│   │   ├── translate.py      # OpenAI <-> Horde format conversion
│   │   ├── templates.py      # Chat template rendering
│   │   ├── filters.py        # Model + worker filtering
│   │   ├── routing.py        # Model alias resolution, "best"/"fast" logic
│   │   └── retry.py          # Auto-retry with backoff
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── openai.py          # OpenAI request/response models
│   │   └── horde.py           # Horde request/response models
│   └── tui/
│       ├── __init__.py
│       ├── app.py             # Textual App, screen routing
│       ├── screens/
│       │   ├── __init__.py
│       │   ├── welcome.py      # First-run API key setup
│       │   ├── dashboard.py    # Home / status screen
│       │   ├── config.py       # Configuration editor
│       │   ├── models.py       # Model browser
│       │   ├── chat.py         # Test chat interface
│       │   ├── logs.py         # Request log viewer
│       │   └── history.py      # Chat history browser
│       └── widgets/
│           ├── __init__.py
│           ├── model_table.py   # Filterable model list
│           ├── chat_view.py     # Chat message display
│           └── kudos_bar.py     # Kudos balance + usage indicator
├── tests/
│   ├── test_translate.py
│   ├── test_routing.py
│   ├── test_filters.py
│   └── test_chat.py
└── data/
    └── templates/            # Built-in chat templates (Phase 2)
```

## TUI (Terminal User Interface)

Interactive terminal app for configuring the proxy and testing it without external tools.
Run via `python -m app.tui` or a `horde-oai-tui` entry point.

### Tech

- **Textual** — modern Python TUI framework (rich-based, async-friendly)

### First Run / API Key Setup

On first launch (no saved config), the TUI shows a welcome screen:

```
┌─ Welcome to ai-horde-oai ───────────────────────┐
│                                                  │
│  Enter your AI Horde API key to get started.     │
│  Get one at: https://aihorde.net/register        │
│                                                  │
│  API Key: [________________________________]     │
│                                                  │
│  [Validate & Save]        [Use Anonymous (0000)] │
└──────────────────────────────────────────────────┘
```

- Validates the key against `GET /v2/find_user` before proceeding
- Shows username and kudos balance on success
- On failure, shows error and lets the user retry
- Saves key to `~/.ai-horde-oai/config.yaml`
- Can also be re-triggered from Config screen to change the key

### Screens

#### 1. Dashboard (home)

```
┌─ ai-horde-oai ──────────────────────────────────┐
│  Server: ● Running on 0.0.0.0:8000              │
│  API Key: ****Hk3f (valid ✓)     Kudos: 12,450  │
│  Models loaded: 23 (filtered from 87)            │
│                                                  │
│  Kudos today: -340    Requests: 12               │
│  Active jobs: 1  (queue pos: 3, ETA: ~12s)       │
│                                                  │
│  [F1 Config] [F2 Models] [F3 Test] [F4 Logs]     │
│  [F5 History] [Q Quit]                           │
└──────────────────────────────────────────────────┘
```

- Shows server status, API key validation, kudos balance
- Kudos balance refreshed periodically via `GET /v2/find_user`
- Quick stats: models available, active requests, kudos spent in session
- Live queue position and ETA for active jobs
- Keybinds to navigate to other screens

#### 2. Config Screen (F1)

Edit all configuration values interactively:

- Horde API Key (input, masked) — with **Validate** button that checks against Horde API
- Horde API URL (input)
- Default model (dropdown from available models)
- Model aliases (editable key-value pairs)
- Host / Port (inputs)
- Trusted workers (toggle)
- Model filters:
  - Min context length (number input)
  - Min max length (number input)
  - Whitelist (comma-separated input)
  - Blocklist (comma-separated input)
- Worker filters:
  - Worker whitelist (comma-separated)
  - Worker blocklist (comma-separated)
- Retry settings:
  - Max retries (number)
  - Timeout (number)
  - Broaden on retry (toggle)
- **Save** writes to `~/.ai-horde-oai/config.yaml`
- **Apply** reloads config in the running server without restart

#### 3. Models Browser (F2)

```
┌─ Models ─────────────────────────────────────────┐
│ Filter: [llama________]  Context ≥ [4096]        │
│                                                  │
│  ✓ aphrodite/llama-3.1-8b    ctx:8192  w:3  q:Q6 │
│  ✓ aphrodite/llama-3.1-70b   ctx:4096  w:1  q:Q4 │
│  ✗ koboldcpp/phi-3-mini      ctx:4096  w:2  q:Q5 │ ← blocked
│  ...                                             │
│                                                  │
│ Aliases: default→llama-3.1-8b  large→llama-3.1-70b│
│ [Enter: details] [Space: toggle] [A: set alias]  │
└──────────────────────────────────────────────────┘
```

- Live-fetched model list from Horde API
- Shows: name, context length, worker count, quantization, queue depth
- Visual indicator of which models pass/fail current filters
- Interactive filter: type to search, adjust min context
- Actions: toggle whitelist/blocklist per model, assign as alias, view details
- Shows current alias mappings at the bottom

#### 4. Test Chat Screen (F3)

Built-in chat interface to test the proxy end-to-end:

```
┌─ Test Chat ──────────────────────────────────────┐
│ Model: [default  ▾]  (→ aphrodite/llama-3.1-8b)  │
│ ─────────────────────────────────────────────    │
│ System: You are a helpful assistant.             │
│                                                  │
│ User: Hello, how are you?                        │
│                                                  │
│ Assistant: I'm doing well! How can I help you?   │
│   ── 2.3s · 42 tokens · worker: gpu-node-7 ──   │
│                                                  │
│ > [Type a message...________________________]    │
│                                                  │
│ Temp: [0.7] Top-P: [0.9] Max: [512]             │
│ [Send] [Clear] [Copy cURL]                       │
└──────────────────────────────────────────────────┘
```

- Sends requests through the local proxy (`localhost:PORT/v1/chat/completions`)
- Model selector uses **dummy names** (dropdown shows aliases)
- Shows resolved real model name next to the alias
- Editable system prompt
- Adjustable generation params (temperature, top_p, max_tokens)
- Shows response metadata: latency, token count, worker name, kudos cost
- **Copy cURL**: copies equivalent curl command to clipboard for debugging
- Chat history within the session

#### 5. Request Log (F4)

```
┌─ Request Log ────────────────────────────────────┐
│ 14:32:05  POST /v1/chat/completions  200  2.3s   │
│ 14:31:58  GET  /v1/models            200  0.4s   │
│ 14:31:12  POST /v1/chat/completions  200  5.1s   │
│ 14:30:45  POST /v1/images/gen...     200  18.2s  │
│                                                  │
│ [Enter: inspect request/response]                │
└──────────────────────────────────────────────────┘
```

- Live tail of all requests hitting the proxy
- Shows: timestamp, method, path, status code, duration
- Select a row to inspect full request/response bodies (JSON viewer)
- Filter by status code or endpoint

#### 6. Chat History (F5)

Persistent log of all test chat conversations:

```
┌─ Chat History ───────────────────────────────────┐
│                                                  │
│  2026-03-08 14:32  default (llama-3.1-8b)  3 msgs  -15k │
│  2026-03-08 13:10  large (mistral-7b)      8 msgs  -42k │
│  2026-03-07 22:45  best (llama-3.1-70b)    2 msgs  -28k │
│  2026-03-07 19:00  default (llama-3.1-8b) 12 msgs  -67k │
│                                                  │
│ [Enter: resume chat] [D: delete] [E: export]     │
└──────────────────────────────────────────────────┘
```

- Saves all test chat sessions to disk (`~/.ai-horde-oai/history/`)
- Each entry shows: date, alias used, resolved model, message count, kudos spent
- **Resume**: re-open a past conversation and continue chatting
- **Delete**: remove a session from history
- **Export**: save conversation as JSON or plain text
- History stored as JSON files, one per session

### Kudos Tracking

The TUI tracks kudos usage across sessions:

- **Live balance**: fetched from `GET /v2/find_user` on dashboard, refreshed every 30s
- **Per-request cost**: each chat completion response includes kudos consumed (from Horde response)
- **Session total**: running sum of kudos spent since TUI launch, shown on dashboard
- **History**: each saved chat session records total kudos spent
- If kudos balance is low (< 100), show a warning on the dashboard

### TUI ↔ Server Interaction

- TUI can **start/stop** the FastAPI server as a background asyncio task
- Config changes in TUI update the shared `Settings` object and write to `config.yaml`
- Test chat sends real HTTP requests to the local proxy (validates full round-trip)
- Request log hooks into FastAPI middleware to capture traffic

## Tech Stack

- **Python 3.11+**
- **FastAPI** — async web framework
- **httpx** — async HTTP client for Horde API calls
- **Pydantic v2** — request/response validation
- **uvicorn** — ASGI server
- **Textual** — TUI framework
- **PyYAML** — config file parsing

## Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install .
EXPOSE 8000
CMD ["python", "-m", "app.main"]
```

```yaml
# docker-compose.yaml
services:
  ai-horde-oai:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./config.yaml:/root/.ai-horde-oai/config.yaml
    environment:
      - HORDE_API_KEY=${HORDE_API_KEY}
    restart: unless-stopped
```

- Headless mode (no TUI) — just the API server
- Config via mounted `config.yaml` or env vars
- Single lightweight container

## Known Limitations (inherited from Horde)

- No true streaming — Horde is async (submit + poll), SSE is simulated
- No function calling / tool use
- No logprobs or logit_bias
- No vision/image input for text models
- Response latency depends on Horde queue and worker availability
- Some models may not support system messages

## Implementation Plan

Detailed step-by-step instructions for building each component.
Each step ends with a **checkpoint** — what must work before moving on.

---

### Phase 1 — Core API

#### Step 1: Project Scaffolding

**Goal**: Runnable FastAPI app with config loading.

**1.1** Create `pyproject.toml`:
```toml
[project]
name = "ai-horde-oai"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "httpx>=0.28",
    "pydantic>=2.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
tui = ["textual>=1.0"]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "respx>=0.22",
    "pytest-cov>=6.0",
    "textual-dev>=1.0",
]

[project.scripts]
horde-oai = "app.main:cli"
horde-oai-tui = "app.tui.app:cli"
```

**1.2** Create `app/config.py`:
- Define `Settings` Pydantic model with all config fields and defaults
- `load_config(path)` function: read YAML, merge with env vars, return `Settings`
- Config path default: `~/.ai-horde-oai/config.yaml`
- Create config dir if it doesn't exist

**1.3** Create `app/main.py`:
- FastAPI app with lifespan (init httpx client, load config)
- `GET /health` → `{"status": "ok"}`
- `cli()` function that runs uvicorn

**1.4** Create `tests/conftest.py`:
- `test_config` fixture with tmp_path
- `mock_horde` fixture with respx
- `client` fixture with ASGITransport

**1.5** Create test fixtures:
- `tests/fixtures/horde_models.json` — 5 sample models with varying context, workers, quant
- `tests/fixtures/horde_generate.json` — completed generation with 1 result
- `tests/fixtures/horde_user.json` — user with username + kudos balance

**Checkpoint**: `pip install -e ".[dev]"` works. `horde-oai` starts server. `GET /health` returns 200. `pytest tests/unit/test_config.py` passes.

---

#### Step 2: Horde Client

**Goal**: Async HTTP client that talks to all needed Horde endpoints.

**2.1** Create `app/horde/__init__.py`

**2.2** Create `app/horde/client.py`:
```python
class HordeClient:
    def __init__(self, base_url: str, api_key: str, client_agent: str):
        self.http = httpx.AsyncClient(base_url=base_url, headers={...})

    async def get_models(self, type="text") -> list[HordeModel]:
        """GET /v2/status/models?type=text"""

    async def get_user(self) -> HordeUser:
        """GET /v2/find_user (requires apikey header)"""

    async def submit_text_job(self, payload: HordeTextRequest) -> str:
        """POST /v2/generate/text/async → returns job ID"""

    async def poll_text_status(self, job_id: str) -> HordeJobStatus:
        """GET /v2/generate/text/status/{id}"""

    async def cancel_text_job(self, job_id: str) -> HordeJobStatus:
        """DELETE /v2/generate/text/status/{id}"""

    async def submit_image_job(self, payload: HordeImageRequest) -> str:
        """POST /v2/generate/async → returns job ID (Phase 3)"""

    async def poll_image_status(self, job_id: str) -> HordeJobStatus:
        """GET /v2/generate/status/{id} (Phase 3)"""
```

**2.3** Create `app/schemas/horde.py`:
- `HordeModel` — name, count (workers), queued, max_length, max_context_length
- `HordeUser` — username, kudos, trusted
- `HordeTextRequest` — prompt, params, models, workers, trusted_workers, worker_blacklist
- `HordeTextParams` — max_length, temperature, top_p, stop_sequence, n
- `HordeJobStatus` — done, finished, processing, waiting, queue_position, wait_time, generations, kudos
- `HordeGeneration` — text, model, worker_id, worker_name

**2.4** Wire `HordeClient` into FastAPI lifespan:
- Create client on startup, store in `app.state`
- Close client on shutdown

**Checkpoint**: All `test_horde_client.py` tests pass against mocked Horde. Client is accessible from routes via `request.app.state.horde`.

---

#### Step 3: Format Translation

**Goal**: Convert between OpenAI and Horde request/response formats.

**3.1** Create `app/schemas/openai.py`:
```python
class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: str | list[str] | None = None
    n: int = 1
    stream: bool = False

class ChatChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str = "stop"

class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatChoice]
    usage: Usage
```

**3.2** Create `app/horde/translate.py`:
- `openai_to_horde(request: ChatCompletionRequest, resolved_model: str) -> HordeTextRequest`
  - Render messages to prompt (simple ChatML for now)
  - Map fields per translation table
- `horde_to_openai(status: HordeJobStatus, model_alias: str) -> ChatCompletionResponse`
  - Map generations to choices
  - Generate response ID (`chatcmpl-{uuid}`)
  - Compute usage from kudos (approximate)

**3.3** Message rendering (simple ChatML):
```python
def render_chatml(messages: list[ChatMessage]) -> str:
    parts = []
    for msg in messages:
        parts.append(f"<|im_start|>{msg.role}\n{msg.content}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)
```

**Checkpoint**: `test_translate.py` passes. Round-trip: create OpenAI request → translate to Horde → mock Horde response → translate back to OpenAI → validate schema.

---

#### Step 4: Model Alias Mapping

**Goal**: Clients use dummy names, proxy resolves to real Horde models.

**4.1** Create `app/horde/routing.py`:
```python
class ModelRouter:
    def __init__(self, config: Settings, horde_client: HordeClient):
        self.aliases = {"default": config.default_model, **config.model_aliases}

    async def resolve(self, alias: str, models: list[HordeModel]) -> str:
        """Resolve dummy name to real Horde model name."""
        if alias == "best":
            return self._pick_best(models)
        if alias == "fast":
            return self._pick_fast(models)
        if alias in self.aliases:
            return self.aliases[alias]
        raise ModelNotFoundError(alias)

    def reverse(self, real_name: str) -> str:
        """Map real Horde model name back to dummy alias."""
        for alias, target in self.aliases.items():
            if target == real_name:
                return alias
        return real_name  # fallback: expose real name

    def get_dummy_list(self) -> list[str]:
        """Return all available dummy names: aliases + best + fast."""
        return ["best", "fast"] + list(self.aliases.keys())

    def _pick_best(self, models: list[HordeModel]) -> str:
        """Model with most active workers."""

    def _pick_fast(self, models: list[HordeModel]) -> str:
        """Model with lowest queue_position / wait_time."""
```

**4.2** Wire `ModelRouter` into app lifespan alongside `HordeClient`.

**Checkpoint**: `test_routing.py` passes. `resolve("default")` returns configured model. `resolve("best")` picks highest-worker model from fixture. `resolve("unknown")` raises error. `get_dummy_list()` returns alias names only.

---

#### Step 5: `/v1/models` Endpoint

**Goal**: OpenAI-compatible model list returning dummy names.

**5.1** Create `app/routers/models.py`:
```python
router = APIRouter(prefix="/v1")

@router.get("/models")
async def list_models(request: Request):
    router = request.app.state.model_router
    dummy_names = router.get_dummy_list()
    return {
        "object": "list",
        "data": [
            {
                "id": name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "ai-horde",
            }
            for name in dummy_names
        ]
    }

@router.get("/models/{model_id}")
async def get_model(model_id: str, request: Request):
    router = request.app.state.model_router
    if model_id not in router.get_dummy_list():
        raise HTTPException(404, detail=f"Model '{model_id}' not found")
    return {"id": model_id, "object": "model", ...}
```

**5.2** Register router in `app/main.py`.

**Checkpoint**: `test_models_endpoint.py` passes. `GET /v1/models` returns dummy names only. Real Horde model names are not exposed. `/v1/models/default` returns 200. `/v1/models/fake` returns 404.

---

#### Step 6: Model Filtering

**Goal**: Filter Horde models by context, length, and name patterns.

**6.1** Create `app/horde/filters.py`:
```python
def filter_models(
    models: list[HordeModel],
    whitelist: list[str] | None = None,
    blocklist: list[str] | None = None,
    min_context: int = 0,
    min_max_length: int = 0,
) -> list[HordeModel]:
    result = models

    # Step 1: whitelist (if set, keep only matching)
    if whitelist:
        result = [m for m in result
                  if any(w.lower() in m.name.lower() for w in whitelist)]

    # Step 2: blocklist (remove matching)
    if blocklist:
        result = [m for m in result
                  if not any(b.lower() in m.name.lower() for b in blocklist)]

    # Step 3: min context
    if min_context > 0:
        result = [m for m in result if m.max_context_length >= min_context]

    # Step 4: min max length
    if min_max_length > 0:
        result = [m for m in result if m.max_length >= min_max_length]

    return result
```

**6.2** Integrate into `ModelRouter` — filter before `best`/`fast` selection.

**6.3** Integrate into `/v1/models` — filtered models determine which aliases are valid.

**Checkpoint**: `test_filters.py` passes (9 cases). `GET /v1/models` with blocklist config hides affected aliases. `best` only considers filtered models.

---

#### Step 7: Worker Filtering

**Goal**: Control which Horde workers can process requests.

**7.1** Extend `app/horde/filters.py`:
```python
def apply_worker_filters(
    request: HordeTextRequest,
    config: Settings,
) -> HordeTextRequest:
    """Mutate Horde request to include worker constraints."""
    if config.trusted_workers:
        request.trusted_workers = True
    if config.worker_whitelist:
        request.workers = config.worker_whitelist  # max 5
    if config.worker_blocklist:
        request.worker_blacklist = config.worker_blocklist
    return request
```

**7.2** Add `workers`, `worker_blacklist` fields to `HordeTextRequest` schema.

**Checkpoint**: `test_filters.py` worker tests pass. Horde request payload includes worker constraints when configured.

---

#### Step 8: `/v1/chat/completions` (Non-Streaming)

**Goal**: Full working chat endpoint — the core feature.

**8.1** Create `app/routers/chat.py`:
```python
router = APIRouter(prefix="/v1")

@router.post("/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    horde = request.app.state.horde
    router = request.app.state.model_router
    config = request.app.state.config

    # 1. Fetch available models (cached)
    models = await horde.get_models()
    filtered = filter_models(models, ...)

    # 2. Resolve alias to real model
    real_model = await router.resolve(body.model, filtered)

    # 3. Translate request
    horde_req = openai_to_horde(body, real_model)
    horde_req = apply_worker_filters(horde_req, config)

    # 4. Submit job
    job_id = await horde.submit_text_job(horde_req)

    # 5. Poll until done
    while True:
        status = await horde.poll_text_status(job_id)
        if status.done:
            break
        await asyncio.sleep(2)

    # 6. Translate response
    alias = router.reverse(real_model)
    return horde_to_openai(status, alias)
```

**8.2** Register router in `app/main.py`.

**8.3** Add model list caching (TTL ~60s) to avoid hitting Horde models endpoint every request.

**Checkpoint**: `test_chat_completions.py` passes. Full round-trip with mocked Horde works. Response matches OpenAI schema. Model name in response is the dummy alias, not real name.

---

#### Step 9: Auto-Retry

**Goal**: Automatically retry failed or timed-out jobs.

**9.1** Create `app/horde/retry.py`:
```python
async def with_retry(
    submit_fn: Callable,
    poll_fn: Callable,
    cancel_fn: Callable,
    config: RetryConfig,
    on_broaden: Callable | None = None,
) -> HordeJobStatus:
    """Submit + poll with retry logic."""
    for attempt in range(1 + config.max_retries):
        if attempt > 0 and config.broaden_on_retry and on_broaden:
            on_broaden()  # relax filters

        job_id = await submit_fn()
        deadline = time.monotonic() + config.timeout_seconds

        while time.monotonic() < deadline:
            status = await poll_fn(job_id)
            if status.done:
                if status.generations:
                    return status
                break  # empty result → retry
            await asyncio.sleep(2)
        else:
            await cancel_fn(job_id)  # timeout → cancel

    raise HordeTimeoutError("All retries exhausted")
```

**9.2** Integrate into `chat.py` — replace the raw poll loop with `with_retry()`.

**9.3** Add `RetryConfig` to `Settings`.

**Checkpoint**: `test_retry.py` passes. E2E: mock Horde fails first call, succeeds second → client gets 200. Mock Horde fails all → client gets 504.

---

#### Step 10: Queue Position / ETA

**Goal**: Track and expose queue position during polling.

**10.1** Add `queue_position`, `wait_time` to `HordeJobStatus` schema.

**10.2** Create an internal `JobTracker` that stores active jobs + their latest queue info:
```python
class JobTracker:
    """Track active jobs for TUI dashboard visibility."""
    active: dict[str, HordeJobStatus] = {}

    def update(self, job_id: str, status: HordeJobStatus): ...
    def remove(self, job_id: str): ...
    def get_active(self) -> list[tuple[str, HordeJobStatus]]: ...
```

**10.3** Wire `JobTracker` into app state. Update it during polling in `retry.py`.

**Checkpoint**: `test_horde_client.py` queue tests pass. `JobTracker` correctly stores/removes active jobs. Queue info accessible from app state.

---

#### Step 11: SSE Streaming

**Goal**: `stream: true` returns Server-Sent Events with queue updates.

**11.1** Extend `chat.py` to handle `stream: true`:
```python
if body.stream:
    return StreamingResponse(
        stream_response(horde, router, body, config),
        media_type="text/event-stream",
    )
```

**11.2** Implement `stream_response()` generator:
```python
async def stream_response(...):
    job_id = await horde.submit_text_job(horde_req)

    # Poll phase — send queue comments
    while True:
        status = await horde.poll_text_status(job_id)
        if status.done:
            break
        if status.queue_position is not None:
            yield f": queue_position={status.queue_position}, eta={status.wait_time}s\n\n"
        await asyncio.sleep(2)

    # Stream the result as chunks
    text = status.generations[0].text
    chunk_size = 4  # tokens (approximate by words)
    for i, word in enumerate(text.split()):
        chunk = build_stream_chunk(word + " ", model_alias, i == 0)
        yield f"data: {json.dumps(chunk)}\n\n"

    yield "data: [DONE]\n\n"
```

**11.3** Create OpenAI stream chunk schema:
```python
class ChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[StreamChoice]

class StreamChoice(BaseModel):
    index: int
    delta: dict  # {"role": "assistant"} or {"content": "..."} or {}
    finish_reason: str | None = None
```

**11.4** Handle client disconnect — if client closes connection, cancel the Horde job.

**Checkpoint**: `test_chat_streaming.py` passes. SSE format correct. Queue comments appear before content. Final event is `[DONE]`. Non-streaming still works (regression).

---

#### Step 12: Smart Model Routing

**Goal**: `best` picks most-available model, `fast` picks lowest-queue model.

**12.1** Implement `_pick_best()` in `ModelRouter`:
- Sort filtered models by `count` (worker count) descending
- Return first (most workers)
- On tie: alphabetical by name (deterministic)

**12.2** Implement `_pick_fast()` in `ModelRouter`:
- Sort filtered models by `queued` ascending (fewest queued jobs)
- If `queued` data unavailable, fall back to `_pick_best()`
- Return first

**12.3** Cache model stats for routing (same cache as step 8.3).

**Checkpoint**: `test_routing.py` best/fast tests pass. E2E: `model: "best"` resolves to expected model in mock data.

---

#### Step 13: Error Handling

**Goal**: All errors return OpenAI-compatible JSON format.

**13.1** Create error handler middleware or exception handlers:
```python
class OpenAIError(BaseModel):
    message: str
    type: str
    param: str | None = None
    code: str | None = None

# Exception handlers
@app.exception_handler(ModelNotFoundError)
async def model_not_found(request, exc):
    return JSONResponse(404, {"error": {"message": str(exc), "type": "invalid_request_error", "code": "model_not_found"}})

@app.exception_handler(HordeAPIError)
async def horde_error(request, exc):
    status_map = {401: 401, 429: 429}
    status = status_map.get(exc.status_code, 502)
    return JSONResponse(status, {"error": {"message": ..., "type": ...}})

@app.exception_handler(HordeTimeoutError)
async def timeout_error(request, exc):
    return JSONResponse(504, {"error": {"message": "Generation timed out", "type": "server_error"}})

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(422, {"error": {"message": str(exc), "type": "invalid_request_error"}})
```

**13.2** Map Horde HTTP status codes:
| Horde | Proxy | OpenAI type |
|---|---|---|
| 401 | 401 | `authentication_error` |
| 429 | 429 | `rate_limit_error` |
| 400 | 400 | `invalid_request_error` |
| 5xx | 502 | `server_error` |
| timeout | 504 | `server_error` |
| unreachable | 502 | `server_error` |

**13.3** Warn on unsupported fields (`tools`, `functions`, `logprobs`) — return 400 with clear message.

**Checkpoint**: `test_error_handling.py` passes. All error responses match `{"error": {"message", "type", "param", "code"}}` schema.

---

#### Step 14: Chat Templates

**Goal**: Render messages using model-appropriate templates.

**14.1** Create `app/horde/templates.py`:
```python
TEMPLATES = {
    "chatml": {
        "system": "<|im_start|>system\n{content}<|im_end|>",
        "user": "<|im_start|>user\n{content}<|im_end|>",
        "assistant": "<|im_start|>assistant\n{content}<|im_end|>",
        "generation_prompt": "<|im_start|>assistant\n",
    },
    "llama3": {
        "system": "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n{content}<|eot_id|>",
        "user": "<|start_header_id|>user<|end_header_id|>\n\n{content}<|eot_id|>",
        "assistant": "<|start_header_id|>assistant<|end_header_id|>\n\n{content}<|eot_id|>",
        "generation_prompt": "<|start_header_id|>assistant<|end_header_id|>\n\n",
    },
    "mistral": {
        "system": None,  # prepend to first user message
        "user": "[INST] {content} [/INST]",
        "assistant": "{content}</s>",
        "generation_prompt": "",
    },
}

def detect_template(model_name: str) -> str:
    """Guess template from model name."""
    name = model_name.lower()
    if "llama-3" in name or "llama3" in name:
        return "llama3"
    if "mistral" in name or "mixtral" in name:
        return "mistral"
    return "chatml"  # default fallback

def render_messages(messages: list[ChatMessage], template_name: str) -> str:
    """Render messages list into a single prompt string."""
    template = TEMPLATES[template_name]
    ...
```

**14.2** Integrate into `translate.py` — use `detect_template()` with resolved model name.

**14.3** Allow config override: `template_override: "chatml"` in config.yaml forces a specific template.

**Checkpoint**: `test_templates.py` passes. ChatML, Llama3, Mistral all render correctly. Unknown model falls back to ChatML. Multi-turn conversations render in order.

---

### Phase 2 — TUI

#### Step 15: TUI App Shell + Welcome Screen

**Goal**: Textual app launches, shows welcome screen on first run.

**15.1** Create `app/tui/app.py`:
```python
from textual.app import App

class HordeOAIApp(App):
    TITLE = "ai-horde-oai"
    BINDINGS = [
        ("s", "switch_screen('config')", "Settings"),
        ("m", "switch_screen('models')", "Models"),
        ("c", "switch_screen('chat')", "Chat"),
        ("l", "switch_screen('logs')", "Logs"),
        ("h", "switch_screen('history')", "History"),
        ("d", "switch_screen('dashboard')", "Dashboard"),
        ("q", "quit", "Quit"),
    ]

    def on_mount(self):
        if not config_exists():
            self.push_screen(WelcomeScreen())
        else:
            self.push_screen(DashboardScreen())

def cli():
    app = HordeOAIApp()
    app.run()
```

**15.2** Create `app/tui/screens/welcome.py`:
- API key input (masked)
- Validate button → calls `HordeClient.get_user()` directly (not through proxy)
- On success: show username + kudos, save config, navigate to dashboard
- On failure: show error inline
- "Use Anonymous" button → save default key, proceed

**Checkpoint**: `horde-oai-tui` launches. First run shows welcome. Valid key → dashboard. Invalid key → error stays on welcome.

---

#### Step 16: Dashboard + Kudos

**Goal**: Dashboard shows server status and kudos.

**16.1** Create `app/tui/screens/dashboard.py`:
- Server status indicator (running/stopped)
- Start/stop server button (runs uvicorn as asyncio task)
- Masked API key display
- Kudos balance (fetched on mount, refreshed every 30s via `set_interval`)
- Session stats: requests served, kudos spent
- Active jobs with queue position/ETA (reads from `JobTracker`)
- Navigation keybinds footer

**16.2** Create `app/tui/widgets/kudos_bar.py`:
- Shows balance as number + visual bar
- Color-coded: green (>1000), yellow (100-1000), red (<100)
- Low-kudos warning text when < 100

**Checkpoint**: Dashboard shows. Server starts/stops. Kudos display updates. F1-F5 navigate.

---

#### Step 17: Config Screen

**Goal**: Edit all settings interactively.

**17.1** Create `app/tui/screens/config.py`:
- Form layout with labeled inputs for every config field
- Grouped into sections: Connection, Models, Workers, Retry
- API key field with "Validate" button (calls Horde, shows ✓/✗)
- Model alias editor: list of key=value rows, add/remove buttons
- Save button → write `config.yaml`
- Apply button → reload `Settings` in app state without restart
- Cancel button → discard changes, return to dashboard

**17.2** Config save logic:
```python
def save_config(settings: Settings, path: Path):
    data = settings.model_dump(exclude_none=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False))
```

**Checkpoint**: All fields editable. Save writes valid YAML. Apply reloads in-memory config. Cancel discards changes.

---

#### Step 18: Models Browser

**Goal**: Browse and filter live Horde models, manage aliases.

**18.1** Create `app/tui/widgets/model_table.py`:
- DataTable with columns: Status (✓/✗), Name, Context, Max Length, Workers, Queued, Quant
- Sortable by any column (click header)
- Search input at top → filters rows in real-time (substring match)

**18.2** Create `app/tui/screens/models.py`:
- On mount: fetch models from Horde, apply filters, populate table
- Refresh button to re-fetch
- Min context input → live filter
- Status column: ✓ if model passes all filters, ✗ if blocked (with reason tooltip)
- Keybinds:
  - `Space` → toggle model in blocklist (updates config)
  - `A` → prompt for alias name, assign model as that alias
  - `Enter` → show detail panel (all model metadata)
- Footer shows current alias mappings

**Checkpoint**: Table populates from mock data. Search filters work. Blocklist toggle updates config. Alias assignment works.

---

#### Step 19: Test Chat

**Goal**: Chat with AI through the proxy, right in the TUI.

**19.1** Create `app/tui/widgets/chat_view.py`:
- Scrollable container of chat bubbles
- Each bubble: role label, content text, metadata footer (latency, tokens, worker, kudos)
- System prompt shown as a dimmed banner at top
- Auto-scroll to bottom on new message

**19.2** Create `app/tui/screens/chat.py`:
- Model selector (Select widget) → populated with dummy names from `ModelRouter`
- Shows resolved real model name as subtitle
- System prompt input (collapsible, default: "You are a helpful assistant.")
- Message input at bottom + Send button (or Enter)
- On send:
  1. Add user message to chat view
  2. Show "Generating..." spinner
  3. `POST` to `http://localhost:{port}/v1/chat/completions` via httpx
  4. Parse response, add assistant message to chat view with metadata
- Parameter bar: temperature, top_p, max_tokens (editable number inputs)
- Clear button → wipe chat, keep system prompt
- Copy cURL button → build curl command from last request, copy to clipboard
- Auto-save session to history on every assistant response

**Checkpoint**: Can select model, type message, get response through proxy. Metadata shows. Params work. cURL copies correctly.

---

#### Step 20: Request Log

**Goal**: Live view of all HTTP traffic through the proxy.

**20.1** Add logging middleware to FastAPI:
```python
class RequestLogMiddleware:
    async def __call__(self, request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start
        log_entry = LogEntry(
            timestamp=datetime.now(),
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration=duration,
            request_body=...,   # captured
            response_body=...,  # captured
        )
        request.app.state.request_log.append(log_entry)
        return response
```

**20.2** Create `app/tui/screens/logs.py`:
- DataTable: Timestamp, Method, Path, Status, Duration
- Color-coded status: green (2xx), yellow (4xx), red (5xx)
- Auto-updates as new requests come in (reactive binding to log list)
- Enter on row → modal with full request/response JSON (pretty-printed)
- Filter bar: status code, path substring

**Checkpoint**: Make request via chat screen → log entry appears. Inspect shows full JSON. Filter works.

---

#### Step 21: Chat History

**Goal**: Persist and browse past chat sessions.

**21.1** Define history file format:
```json
{
  "id": "uuid",
  "created_at": "2026-03-08T14:32:00",
  "model_alias": "default",
  "resolved_model": "aphrodite/llama-3.1-8b",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi!", "metadata": {"latency": 2.3, "tokens": 5, "worker": "...", "kudos": 15}}
  ],
  "total_kudos": 15,
  "params": {"temperature": 0.7, "top_p": 0.9, "max_tokens": 512}
}
```

**21.2** History manager:
```python
class HistoryManager:
    def __init__(self, history_dir: Path):
        self.dir = history_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def save_session(self, session: ChatSession): ...
    def list_sessions(self) -> list[ChatSessionSummary]: ...
    def load_session(self, session_id: str) -> ChatSession: ...
    def delete_session(self, session_id: str): ...
    def export_json(self, session_id: str, output: Path): ...
    def export_text(self, session_id: str, output: Path): ...
```

**21.3** Create `app/tui/screens/history.py`:
- DataTable: Date, Model Alias, Resolved Model, Messages, Kudos
- Sorted by date descending
- Keybinds:
  - `Enter` → load session into chat screen, continue chatting
  - `D` → confirm dialog → delete from disk
  - `E` → prompt format (JSON/text) → save to file, show path
- Empty state: "No conversations yet" message

**21.4** Wire auto-save into chat screen (step 19) — save after every assistant response.

**Checkpoint**: Chat session auto-saved. History lists it. Resume works (messages restored). Delete removes file. Export writes valid JSON/text.

---

### Phase 3 — Image Generation

#### Step 22: Image Endpoint

**Goal**: DALL-E compatible image generation via Horde.

**22.1** Create `app/schemas/openai.py` additions:
```python
class ImageGenerationRequest(BaseModel):
    prompt: str
    model: str = "default-image"
    n: int = 1
    size: str = "1024x1024"
    quality: str = "standard"
    response_format: str = "url"  # or "b64_json"

class ImageObject(BaseModel):
    url: str | None = None
    b64_json: str | None = None
    revised_prompt: str | None = None

class ImageGenerationResponse(BaseModel):
    created: int
    data: list[ImageObject]
```

**22.2** Extend `app/horde/translate.py`:
- `openai_image_to_horde()`: parse size → width/height, quality → steps, etc.
- `horde_image_to_openai()`: map Horde image URLs/base64 to OpenAI format

**22.3** Create `app/routers/images.py`:
- `POST /v1/images/generations` — same flow as text: resolve alias → translate → submit → poll → respond
- Handle `response_format: "b64_json"` by downloading image and encoding

**22.4** Add image job support to `HordeClient` (submit_image_job, poll_image_status).

**Checkpoint**: `test_images_endpoint.py` passes. Valid request → image response. Size parsed correctly. Multiple images work.

---

#### Step 23: Image Model Aliases

**Goal**: Separate alias config for image models.

**23.1** Add to config:
```yaml
image_aliases:
  default-image: "stable_diffusion_xl"
  fast-image: "stable_diffusion"
```

**23.2** Extend `ModelRouter` with `resolve_image()` method using image-specific aliases.

**Checkpoint**: Image aliases resolve independently from text aliases. Default image model works.

---

#### Step 24: TUI Image Screen (Stretch)

**Goal**: Test image generation from TUI.

**24.1** Add image generation screen:
- Prompt input
- Model selector (image aliases)
- Size selector dropdown
- Quality toggle (standard/hd)
- Generate button → shows spinner → displays image URL or opens in browser
- History integration (save prompts + results)

**Checkpoint**: Can generate image from TUI. URL displayed. Prompt saved to history.

---

### Phase 4 — Distribution

#### Step 25: Docker

**Goal**: One-command deployment.

**25.1** Create `Dockerfile`:
- Python 3.11-slim base
- Copy project, pip install
- Expose 8000
- CMD runs headless server

**25.2** Create `docker-compose.yaml`:
- Service definition with port mapping
- Volume mount for config.yaml
- Environment variable for HORDE_API_KEY
- `restart: unless-stopped`

**25.3** Add `.dockerignore`:
- Exclude `.git`, `tests/`, `__pycache__/`, `.env`, `*.pyc`

**Checkpoint**: `docker build -t ai-horde-oai .` succeeds. `docker compose up` starts. `curl localhost:8000/v1/models` responds.

---

#### Step 26: pip Package

**Goal**: `pip install ai-horde-oai` just works.

**26.1** Finalize `pyproject.toml`:
- Entry points: `horde-oai` (server), `horde-oai-tui` (TUI)
- Classifiers, license, URLs
- TUI as optional dependency (`pip install ai-horde-oai[tui]`)

**26.2** Verify:
- `pip install .` in clean venv → works
- `horde-oai` starts server
- `horde-oai-tui` starts TUI (if `[tui]` installed)
- `pip install ai-horde-oai[dev]` installs test deps

**Checkpoint**: Clean install works. Both entry points functional. No import errors.

---

### Milestone Summary

| Milestone | Steps | Result |
|---|---|---|
| **M1: Skeleton** | 1 | App starts, config loads, healthcheck works |
| **M2: Horde Connected** | 1-2 | Can talk to Horde API (mocked) |
| **M3: Models Listed** | 1-5 | `GET /v1/models` returns dummy names |
| **M4: Chat Works** | 1-8 | `POST /v1/chat/completions` returns AI response |
| **M5: Production Ready** | 1-14 | Streaming, retry, errors, templates all working |
| **M6: TUI Usable** | 15-17 | Can configure and monitor via TUI |
| **M7: TUI Complete** | 15-21 | Full TUI with chat, history, logs |
| **M8: Image Gen** | 22-24 | Image generation endpoint works |
| **M9: Distributable** | 25-26 | Docker + pip installable |

## Testing Strategy

### Tooling

- **pytest** + **pytest-asyncio** — test runner
- **httpx** (`ASGITransport`) — test the FastAPI app without starting a real server
- **respx** — mock outbound HTTP calls to AI Horde API
- **pytest-cov** — coverage reporting
- **textual[dev]** — Textual's built-in pilot for TUI testing

### Test Structure

```
tests/
├── conftest.py                # Shared fixtures (mock Horde responses, test config, app client)
├── fixtures/
│   ├── horde_models.json       # Sample Horde /v2/status/models response
│   ├── horde_generate.json     # Sample Horde text generation response
│   ├── horde_image.json        # Sample Horde image generation response
│   └── horde_user.json         # Sample Horde /v2/find_user response
├── unit/
│   ├── test_config.py
│   ├── test_translate.py
│   ├── test_filters.py
│   ├── test_routing.py
│   ├── test_retry.py
│   ├── test_templates.py
│   └── test_horde_client.py
├── e2e/
│   ├── test_models_endpoint.py
│   ├── test_chat_completions.py
│   ├── test_chat_streaming.py
│   ├── test_images_endpoint.py
│   ├── test_error_handling.py
│   └── test_full_flow.py
└── tui/
    ├── test_welcome.py
    ├── test_dashboard.py
    ├── test_config_screen.py
    ├── test_models_browser.py
    ├── test_chat_screen.py
    ├── test_logs_screen.py
    └── test_history_screen.py
```

### Key Fixtures (`conftest.py`)

```python
# Mock Horde API — all outbound calls are intercepted
@pytest.fixture
def mock_horde(respx_mock):
    """Pre-configured mock for all Horde API endpoints."""
    respx_mock.get("/v2/status/models").mock(return_value=Response(200, json=MODELS_FIXTURE))
    respx_mock.post("/v2/generate/text/async").mock(return_value=Response(202, json={"id": "test-job-id"}))
    respx_mock.get("/v2/generate/text/status/test-job-id").mock(return_value=Response(200, json=GENERATE_FIXTURE))
    respx_mock.get("/v2/find_user").mock(return_value=Response(200, json=USER_FIXTURE))
    return respx_mock

# Test app client — hits FastAPI directly via ASGI, no real server
@pytest.fixture
def client(mock_horde):
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")

# Test config — isolated temp dir, no side effects
@pytest.fixture
def test_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(TEST_CONFIG_YAML)
    return load_config(config_file)
```

---

## Implementation Order & Test Plan

Each step lists what to build and what tests to write **before moving on**.

### Phase 1 — Core API

#### Step 1: Project scaffolding

**Build:**
- `pyproject.toml` with dependencies
- `app/__init__.py`, `app/main.py` (empty FastAPI app)
- `app/config.py` (load `config.yaml` + env var overrides)
- `tests/conftest.py` with basic fixtures

**Unit tests** (`tests/unit/test_config.py`):
- Load config from YAML file
- Env vars override YAML values
- Missing config file → defaults applied
- Invalid YAML → clear error
- Partial config (only some keys set) → defaults fill the rest

**E2E tests** (`tests/e2e/test_full_flow.py`):
- App starts and responds to `GET /` or healthcheck with 200

---

#### Step 2: Horde client

**Build:**
- `app/horde/client.py` — async functions: `submit_text_job()`, `poll_job_status()`, `cancel_job()`, `get_models()`, `get_user()`

**Unit tests** (`tests/unit/test_horde_client.py`):
- `submit_text_job()` sends correct payload, returns job ID
- `poll_job_status()` returns parsed status with `done`, `generations`, `queue_position`
- `poll_job_status()` on incomplete job returns `done=False`
- `cancel_job()` sends DELETE, returns partial results
- `get_models()` returns parsed model list
- `get_user()` returns username + kudos
- HTTP errors (401, 429, 500) raise appropriate exceptions
- Timeout handling

---

#### Step 3: Format translation

**Build:**
- `app/horde/translate.py` — `openai_to_horde()`, `horde_to_openai()`
- `app/schemas/openai.py` — Pydantic models for OpenAI request/response
- `app/schemas/horde.py` — Pydantic models for Horde request/response

**Unit tests** (`tests/unit/test_translate.py`):
- `openai_to_horde()`:
  - Maps `max_tokens` → `params.max_length`
  - Maps `temperature`, `top_p`, `stop` correctly
  - Messages rendered into prompt string
  - Missing optional fields → Horde defaults
  - `n` parameter forwarded
- `horde_to_openai()`:
  - Generations mapped to `choices[]`
  - `kudos` mapped to `usage`
  - Multiple generations → multiple choices with correct indices
  - Empty generations → empty choices
- Pydantic models:
  - Valid OpenAI request passes validation
  - Invalid request (missing `messages`) → validation error
  - Extra fields ignored

---

#### Step 4: Model alias mapping + dummy names

**Build:**
- `app/horde/routing.py` — `resolve_model()`, `get_dummy_models()`, `register_alias()`

**Unit tests** (`tests/unit/test_routing.py`):
- `resolve_model("default")` → configured default model name
- `resolve_model("large")` → custom alias value
- `resolve_model("unknown")` → error
- `resolve_model("best")` → picks model with most workers (given mock model list)
- `resolve_model("fast")` → picks model with shortest queue
- `get_dummy_models()` returns list of alias names, not real model names
- Response maps real model name back to dummy name

---

#### Step 5: `/v1/models` endpoint

**Build:**
- `app/routers/models.py` — `GET /v1/models`, `GET /v1/models/{model}`

**Unit tests** (`tests/unit/test_routing.py` — additions):
- Dummy model list matches configured aliases + built-in names

**E2E tests** (`tests/e2e/test_models_endpoint.py`):
- `GET /v1/models` returns OpenAI-format model list with dummy names
- `GET /v1/models` does NOT contain real Horde model names
- `GET /v1/models/default` returns single model object
- `GET /v1/models/nonexistent` returns 404
- Response schema matches OpenAI spec (`id`, `object`, `created`, `owned_by`)

---

#### Step 6: Model filtering

**Build:**
- `app/horde/filters.py` — `filter_models()`

**Unit tests** (`tests/unit/test_filters.py`):
- Whitelist `["llama"]` keeps only models with "llama" in name
- Blocklist `["phi"]` removes models with "phi" in name
- Whitelist + blocklist combined: whitelist first, then blocklist removes
- `min_context=4096` removes models with context < 4096
- `min_max_length=512` removes models that can't generate 512 tokens
- Empty whitelist = allow all
- Case-insensitive matching
- All filters empty → all models pass
- All filters active → correct intersection

**E2E tests** (`tests/e2e/test_models_endpoint.py` — additions):
- `GET /v1/models` with whitelist config → only matching aliases shown
- `GET /v1/models` with blocklist config → blocked models excluded from `best`/`fast` routing

---

#### Step 7: Worker filtering

**Build:**
- Extend `app/horde/filters.py` — `apply_worker_filters()`
- Add worker filter fields to Horde request schema

**Unit tests** (`tests/unit/test_filters.py` — additions):
- Worker whitelist → only listed workers in request `workers[]`
- Worker blocklist → listed workers in `worker_blacklist[]`
- Trusted workers toggle → `trusted_workers: true` in request
- Combined: whitelist + trusted
- Empty filters → no worker constraints in request

---

#### Step 8: `/v1/chat/completions` (non-streaming)

**Build:**
- `app/routers/chat.py` — `POST /v1/chat/completions`
- Full pipeline: receive OpenAI request → resolve alias → translate → submit to Horde → poll → translate back → respond

**E2E tests** (`tests/e2e/test_chat_completions.py`):
- Valid request with `model: "default"` → 200 with OpenAI-format response
- Response contains `choices[].message.content` with generated text
- Response contains `model` field with dummy name (not real Horde name)
- Response contains `usage` field
- `model: "best"` resolves and returns successfully
- `model: "nonexistent"` → 404 error
- Missing `messages` → 422 validation error
- Worker filters applied in outbound Horde request
- Model filters prevent blocked model from being used

---

#### Step 9: Auto-retry

**Build:**
- `app/horde/retry.py` — retry wrapper with backoff logic

**Unit tests** (`tests/unit/test_retry.py`):
- Job succeeds on first try → no retry
- Job fails once, succeeds on retry → returns result
- Job times out → retry triggered
- Empty response → retry triggered
- Max retries exhausted → raises error
- `broaden_on_retry=true` → worker filters relaxed on 2nd attempt
- `broaden_on_retry=false` → same filters on all attempts
- Retry count configurable (0 = no retries)

**E2E tests** (`tests/e2e/test_chat_completions.py` — additions):
- Horde returns failure → proxy retries and succeeds on 2nd call
- Horde times out on all retries → proxy returns 504 with OpenAI error format

---

#### Step 10: Queue position / ETA tracking

**Build:**
- Extend poll loop to capture `queue_position`, `wait_time` from Horde status
- Expose queue info internally for TUI/dashboard consumption

**Unit tests** (`tests/unit/test_horde_client.py` — additions):
- Poll response with `queue_position=5, wait_time=30` parsed correctly
- Poll response without queue fields → defaults to None

---

#### Step 11: `/v1/chat/completions` (streaming via SSE)

**Build:**
- Extend `app/routers/chat.py` — handle `stream: true`
- SSE response: queue comments → content chunks → `[DONE]`

**E2E tests** (`tests/e2e/test_chat_streaming.py`):
- `stream: true` → response is `text/event-stream`
- SSE events contain `data:` lines with OpenAI chunk format
- Final event is `data: [DONE]`
- Chunks contain `delta.content` (not `message.content`)
- Queue position sent as SSE comments (`: queue_position=...`)
- `stream: false` → normal JSON response (regression check)
- Client disconnect during poll → Horde job cancelled

---

#### Step 12: Smart model routing

**Build:**
- Implement `best` and `fast` selection logic in `app/horde/routing.py`
- `best`: highest worker count among filtered models
- `fast`: lowest queue depth / wait time

**Unit tests** (`tests/unit/test_routing.py` — additions):
- `best` with 3 models → picks one with most workers
- `best` with tie → deterministic pick (alphabetical or first)
- `fast` with queue data → picks lowest queue
- `fast` with no queue data → falls back to `best`
- No eligible models after filtering → clear error

**E2E tests** (`tests/e2e/test_chat_completions.py` — additions):
- `model: "best"` → Horde request contains the expected resolved model
- `model: "fast"` → Horde request contains the expected resolved model

---

#### Step 13: Error handling

**Build:**
- OpenAI-compatible error response format across all endpoints
- Map Horde errors to appropriate HTTP status codes

**E2E tests** (`tests/e2e/test_error_handling.py`):
- Horde 401 (invalid key) → proxy returns 401 with `{"error": {"message": ..., "type": "authentication_error"}}`
- Horde 429 (rate limited) → proxy returns 429 with `retry-after`
- Horde 500 → proxy returns 502 (bad gateway)
- Horde unreachable → proxy returns 502
- Invalid JSON body → 422 with field-level errors
- Unsupported field (`tools`, `functions`) → 400 with clear message
- All error responses match OpenAI error schema: `{"error": {"message", "type", "param", "code"}}`

---

#### Step 14: Chat template support

**Build:**
- `app/horde/templates.py` — built-in templates for ChatML, Llama, Mistral
- Auto-detect template from model name

**Unit tests** (`tests/unit/test_templates.py`):
- ChatML template renders system/user/assistant correctly
- Llama template uses `[INST]` / `[/INST]` format
- Mistral template renders correctly
- Unknown model → falls back to ChatML
- Empty system message → omitted from prompt
- Multi-turn conversation rendered in correct order
- Special characters in messages escaped properly

---

### Phase 2 — TUI

All TUI tests use Textual's `pilot` for headless interaction.

#### Step 15: Welcome screen

**Build:**
- `app/tui/screens/welcome.py`

**TUI tests** (`tests/tui/test_welcome.py`):
- Welcome screen shown when no config exists
- Enter key → validates against mock Horde API
- Valid key → shows username + kudos, proceeds to dashboard
- Invalid key → shows error, stays on welcome screen
- "Use Anonymous" → saves anon key, proceeds to dashboard
- Key saved to config file on success

---

#### Step 16: Dashboard + kudos tracking

**Build:**
- `app/tui/screens/dashboard.py`
- `app/tui/widgets/kudos_bar.py`

**TUI tests** (`tests/tui/test_dashboard.py`):
- Dashboard shows server status (running/stopped)
- Dashboard shows masked API key
- Dashboard shows kudos balance from mock API
- Kudos refreshes on timer (mock timer tick → new value displayed)
- Low kudos (< 100) shows warning
- F1-F5 keybinds navigate to correct screens
- Q quits the app
- Active job shows queue position and ETA

---

#### Step 17: Config screen

**Build:**
- `app/tui/screens/config.py`

**TUI tests** (`tests/tui/test_config_screen.py`):
- All config fields displayed with current values
- Edit API key → masked input
- Validate button → calls Horde API, shows result
- Change port → reflected in config
- Toggle trusted workers → value updates
- Edit model whitelist → comma-separated parsed correctly
- Save → writes config.yaml to disk (verify file contents)
- Apply → config reloaded without restart (verify in-memory config changed)
- Cancel → changes discarded

---

#### Step 18: Models browser

**Build:**
- `app/tui/screens/models.py`
- `app/tui/widgets/model_table.py`

**TUI tests** (`tests/tui/test_models_browser.py`):
- Model list populated from mock Horde API
- Shows name, context, workers, quantization columns
- Filtered models marked with ✗ indicator
- Type in filter → table filters in real-time
- Adjust min context → models below threshold hidden
- Space on a model → toggles blocklist
- A on a model → assigns as alias (prompts for alias name)
- Enter on a model → shows detail panel
- Alias mappings shown at bottom

---

#### Step 19: Test chat

**Build:**
- `app/tui/screens/chat.py`
- `app/tui/widgets/chat_view.py`

**TUI tests** (`tests/tui/test_chat_screen.py`):
- Model dropdown shows dummy names
- Resolved model name shown next to alias
- Type message + Send → request sent to local proxy
- Response displayed with content + metadata (latency, tokens, worker)
- Kudos cost shown per response
- Adjust temperature → next request uses new value
- Clear → chat history wiped
- Copy cURL → clipboard contains valid curl command
- Multiple messages → conversation displayed in order

---

#### Step 20: Request log

**Build:**
- `app/tui/screens/logs.py`
- FastAPI middleware for request/response capture

**TUI tests** (`tests/tui/test_logs_screen.py`):
- Log starts empty
- After a request through the proxy → new log entry appears
- Entry shows timestamp, method, path, status, duration
- Enter on entry → shows request/response JSON
- Filter by status code → only matching entries shown
- Multiple requests → shown in reverse chronological order

---

#### Step 21: Chat history

**Build:**
- `app/tui/screens/history.py`
- History persistence (JSON files in `~/.ai-horde-oai/history/`)

**TUI tests** (`tests/tui/test_history_screen.py`):
- After a test chat session → entry appears in history
- Entry shows date, alias, resolved model, message count, kudos
- Enter on entry → opens chat screen with conversation restored
- Resume chat → can send new messages in existing conversation
- D on entry → deletes from disk (confirm prompt)
- E on entry → exports as JSON (verify file written)
- E on entry → exports as plain text (verify format)
- Empty history → shows "No conversations yet" message

---

### Phase 3 — Image Generation

#### Step 22: `/v1/images/generations` endpoint

**Build:**
- `app/routers/images.py`
- Extend Horde client: `submit_image_job()`, `poll_image_status()`
- Image-specific translation in `translate.py`

**Unit tests** (`tests/unit/test_translate.py` — additions):
- OpenAI image request → Horde image request mapping
- `size: "1024x1024"` → `width: 1024, height: 1024`
- `quality: "hd"` → `steps: 50`
- `response_format: "b64_json"` vs `"url"` handled

**E2E tests** (`tests/e2e/test_images_endpoint.py`):
- Valid image request → 200 with OpenAI-format response
- Response contains `data[].url` or `data[].b64_json`
- `n: 2` → two images returned
- Invalid size format → 422
- Model alias resolved for image models

---

#### Step 23: Image model alias mapping

**Build:**
- Extend `routing.py` for image model aliases
- Separate config section for image model aliases

**Unit tests** (`tests/unit/test_routing.py` — additions):
- Image alias resolution separate from text aliases
- Default image model configured

---

#### Step 24: TUI image generation screen (stretch)

**Build:**
- Image generation test screen in TUI

**TUI tests:**
- Prompt input + generate → image request sent
- Response shows image (or URL in terminal)

---

### Phase 4 — Distribution

#### Step 25: Docker

**Build:**
- `Dockerfile`
- `docker-compose.yaml`

**E2E tests** (manual or CI):
- `docker build` succeeds
- Container starts and `GET /v1/models` responds
- Config mounted via volume works
- Env var override works

---

#### Step 26: pip installable package

**Build:**
- Entry points in `pyproject.toml`: `horde-oai` (server), `horde-oai-tui` (TUI)

**E2E tests:**
- `pip install .` succeeds
- `horde-oai` starts the server
- `horde-oai-tui` starts the TUI

---

### CI Pipeline

```yaml
# .github/workflows/test.yaml
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - run: pytest tests/unit/ -v --cov=app --cov-report=term
      - run: pytest tests/e2e/ -v
      - run: pytest tests/tui/ -v
```

### Coverage Targets

| Area | Target |
|---|---|
| `app/config.py` | 95% |
| `app/horde/translate.py` | 100% |
| `app/horde/filters.py` | 100% |
| `app/horde/routing.py` | 95% |
| `app/horde/retry.py` | 90% |
| `app/horde/client.py` | 85% (HTTP edge cases) |
| `app/routers/*` | 90% |
| `app/tui/*` | 75% (TUI testing has limitations) |
| **Overall** | **85%+** |
