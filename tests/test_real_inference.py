"""Integration E2E test with real inference to AI Horde."""
from __future__ import annotations

import os
import time
import pytest
import httpx
from app.config import Settings, RetrySettings
from app.main import create_app, lifespan

# Skip this test by default unless explicitly requested via environment variable
# as it depends on external API and can be slow/unreliable
REAL_INFERENCE = os.getenv("REAL_INFERENCE", "false").lower() == "true"

@pytest.mark.skipif(not REAL_INFERENCE, reason="REAL_INFERENCE env var not set to true")
@pytest.mark.asyncio
async def test_real_inference_phi3():
    """
    Test a real round-trip to AI Horde using a tiny model.
    Model: koboldcpp/phi-3-mini-4k (matches 'kobbletiny' intent for a small fast model)
    """
    config = Settings(
        horde_api_key=os.getenv("HORDE_API_KEY", "0000000000"),
        horde_api_url="https://aihorde.net/api",
        default_model="aphrodite/QuasiStarSynth-12B",
        # Use short timeout but enough for a tiny model
        retry=RetrySettings(max_retries=1, timeout_seconds=120, broaden_on_retry=True)
    )
    
    app = create_app(config)
    
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            print("\nSending real inference request to AI Horde...")
            start_time = time.time()
            
            response = await client.post("/v1/chat/completions", json={
                "model": "default",
                "messages": [
                    {"role": "user", "content": "Say 'Hello World' and nothing else."}
                ],
                "max_tokens": 20
            })
            
            duration = time.time() - start_time
            print(f"Request took {duration:.2f} seconds")
            
            assert response.status_code == 200, f"Error: {response.text}"
            data = response.json()
            
            assert "choices" in data
            content = data["choices"][0]["message"]["content"].strip()
            print(f"Received content: {content}")
            assert "Hello" in content or "world" in content.lower()
            assert data["model"] == "default"
