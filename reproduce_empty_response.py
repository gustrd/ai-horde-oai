"""
Reproduce the empty-response (raw_len=0) case from 2026-03-10 21:22:33.

Log context:
  Model:     default -> DavidAU/GPT-OSS-120b-NEO-High
  Worker:    Belarrius Studio | RP Worker (f81bb3c4-54e8-4fc9-b279-4478b1c58e88)
  Tokens in: ~7,299  18 messages / 29,224 chars
  Result:    retry / empty response (raw_len=0)

Run with:
  uv run python reproduce_empty_response.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

PROXY_URL  = "http://127.0.0.1:8000"
HORDE_URL  = "https://aihorde.net/api"
HORDE_KEY  = "qv_7xsM1mejOqx6Hs7RYdg"
REAL_MODEL = "DavidAU/GPT-OSS-120b-NEO-High"
WORKER_ID  = "f81bb3c4-54e8-4fc9-b279-4478b1c58e88"  # Belarrius Studio | RP Worker

# Load the exact messages from the saved log entry
entry = json.loads(Path("_repro_entry.json").read_text(encoding="utf-8"))
messages = entry["messages"]
total_chars = sum(len(str(m.get("content", ""))) for m in messages)
print(f"Loaded {len(messages)} messages, {total_chars:,} chars ~= {total_chars // 4:,} tokens")
print()


def check_result(content: str | None, tool_calls, status_code: int, elapsed: float) -> None:
    print(f"  HTTP {status_code}  ({elapsed:.1f}s)")
    if status_code != 200:
        return
    if not content and not tool_calls:
        print("  !! EMPTY RESPONSE -- reproduced the bug!")
    else:
        length = len(content or "")
        preview = (content or "")[:200].replace("\n", " ")
        print(f"  content ({length} chars): {preview!r}")


# ── Step 1: via proxy, general pool ─────────────────────────────────────────
print("=== Step 1: via proxy (general pool) ===")
t0 = time.monotonic()
try:
    with httpx.Client(timeout=400.0) as client:
        r = client.post(
            f"{PROXY_URL}/v1/chat/completions",
            json={"model": "default", "messages": messages, "max_tokens": 256, "stream": False},
        )
except httpx.ConnectError:
    print("  ERROR: Cannot connect. Is the server running on port 8000?")
    sys.exit(1)
elapsed = time.monotonic() - t0
d = r.json() if r.status_code == 200 else {}
choice = (d.get("choices") or [{}])[0]
msg = choice.get("message", {})
check_result(msg.get("content"), msg.get("tool_calls"), r.status_code, elapsed)
print()


# ── Step 2: direct Horde API, worker pinned ──────────────────────────────────
print(f"=== Step 2: direct Horde API, worker pinned ({WORKER_ID}) ===")

# Render messages to chatml prompt (same as proxy does for this model)
parts = []
for m in messages:
    role = m.get("role", "user")
    content = str(m.get("content") or "")
    if role == "system":
        parts.append(f"<|im_start|>system\n{content}<|im_end|>\n")
    elif role == "user":
        parts.append(f"<|im_start|>user\n{content}<|im_end|>\n")
    elif role == "assistant":
        parts.append(f"<|im_start|>assistant\n{content}<|im_end|>\n")
parts.append("<|im_start|>assistant\n")
prompt = "".join(parts)
print(f"  Prompt length: {len(prompt):,} chars")

horde_payload = {
    "prompt": prompt,
    "models": [REAL_MODEL],
    "workers": [WORKER_ID],
    "params": {
        "max_length": 256,
        "max_context_length": 16384,
        "temperature": 0.7,
        "top_p": 0.9,
    },
    "trusted_workers": False,
    "slow_workers": True,
}

t0 = time.monotonic()
with httpx.Client(timeout=400.0) as client:
    sub = client.post(
        f"{HORDE_URL}/v2/generate/text/async",
        json=horde_payload,
        headers={"apikey": HORDE_KEY, "Content-Type": "application/json"},
    )
    if sub.status_code not in (200, 202):
        print(f"  Submit failed: HTTP {sub.status_code}  {sub.text[:300]}")
        sys.exit(1)
    job_id = sub.json().get("id")
    print(f"  Submitted job_id={job_id}")

    for _ in range(150):
        time.sleep(2)
        poll = client.get(
            f"{HORDE_URL}/v2/generate/text/status/{job_id}",
            headers={"apikey": HORDE_KEY},
        )
        ps = poll.json()
        if ps.get("faulted"):
            print(f"  Horde faulted the job after {time.monotonic() - t0:.0f}s")
            break
        if ps.get("done"):
            gens = ps.get("generations", [])
            elapsed = time.monotonic() - t0
            if not gens:
                print(f"  Done but no generations ({elapsed:.1f}s)")
            else:
                g = gens[0]
                raw = g.get("text", "")
                print(f"  Worker: {g.get('worker_name', '?')}")
                print(f"  raw_len={len(raw)}")
                if not raw.strip():
                    print("  !! EMPTY RESPONSE -- reproduced the bug!")
                else:
                    print(f"  content: {raw[:300]!r}")
            break
        q = ps.get("queue_position")
        print(f"  ... queue_pos={q}  wait={ps.get('wait_time')}s", end="\r", flush=True)
    else:
        print("\n  Timed out.")
