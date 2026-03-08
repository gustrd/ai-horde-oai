from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_list_models(client):
    r = await client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    ids = [m["id"] for m in data["data"]]
    assert "default" in ids
    assert "best" in ids
    assert "fast" in ids
    assert "large" in ids


@pytest.mark.asyncio
async def test_get_model(client):
    r = await client.get("/v1/models/default")
    assert r.status_code == 200
    assert r.json()["id"] == "default"


@pytest.mark.asyncio
async def test_get_model_not_found(client):
    r = await client.get("/v1/models/nonexistent-xyz-model")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_chat_completions(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "default",
        "messages": [{"role": "user", "content": "Hello!"}],
    })
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) == 1
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["message"]["content"] != ""
    assert data["model"] == "default"


@pytest.mark.asyncio
async def test_chat_completions_with_alias(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "large",
        "messages": [{"role": "user", "content": "Hello!"}],
    })
    assert r.status_code == 200
    assert r.json()["model"] == "large"


@pytest.mark.asyncio
async def test_chat_completions_best(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "best",
        "messages": [{"role": "user", "content": "Hello!"}],
    })
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_legacy_completions(client):
    r = await client.post("/v1/completions", json={
        "model": "default",
        "prompt": "The sky is",
        "max_tokens": 50,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "text_completion"
    assert len(data["choices"]) == 1


@pytest.mark.asyncio
async def test_chat_streaming(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "default",
        "messages": [{"role": "user", "content": "Hello!"}],
        "stream": True,
    })
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    content = r.text
    assert "data:" in content
    assert "[DONE]" in content
