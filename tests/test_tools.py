"""Tests de las tools (flujo feliz y errores), con el core simulado.

Se usa `monkeypatch` sobre `whisper_mcp.core_client` para que los tests no
dependan del mock core ni hagan HTTP.
"""

from __future__ import annotations

import pytest
from mcp import Client

from whisper_mcp import core_client
from whisper_mcp.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_transcribe_ok(client, monkeypatch):
    async def fake_transcribe_bytes(audio, filename, **kwargs):
        return {
            "text": "hola mundo",
            "language": "es",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hola mundo"}],
        }

    monkeypatch.setattr(core_client, "transcribe_bytes", fake_transcribe_bytes)

    res = await client.call_tool("transcribe", {"audio_base64": "aGVsbG8=", "language": "es"})
    assert res.is_error is False
    assert res.structured_content["text"] == "hola mundo"
    assert res.structured_content["segments"][0]["text"] == "hola mundo"


@pytest.mark.anyio
async def test_transcribe_no_source_is_error(client):
    res = await client.call_tool("transcribe", {})
    assert res.is_error is True
    assert "exactamente UNA" in res.content[0].text


@pytest.mark.anyio
async def test_get_unknown_job_is_error(client, monkeypatch):
    from whisper_mcp.core_client import CoreError

    async def fake_get_job(job_id):
        raise CoreError("job_id no encontrado en el core (404).", 404)

    monkeypatch.setattr(core_client, "get_job", fake_get_job)

    res = await client.call_tool("get_transcription", {"job_id": "job_noexiste"})
    assert res.is_error is True
    assert "job_id no encontrado" in res.content[0].text


@pytest.mark.anyio
async def test_submit_get_cancel_flow(client, monkeypatch):
    async def fake_submit(payload):
        return {"job_id": "job_test", "status": "queued", "accepted": True}

    async def fake_get(job_id):
        return {
            "status": "done",
            "text": "texto final",
            "segments": [{"start": 0.0, "end": 2.0, "text": "texto final"}],
            "progress": 1.0,
        }

    async def fake_cancel(job_id):
        return {"cancelled": True}

    monkeypatch.setattr(core_client, "submit_job", fake_submit)
    monkeypatch.setattr(core_client, "get_job", fake_get)
    monkeypatch.setattr(core_client, "cancel_job", fake_cancel)

    sub = await client.call_tool("submit_transcription", {"audio_url": "http://example.com/a.wav"})
    assert sub.structured_content["job_id"] == "job_test"
    assert sub.structured_content["accepted"] is True

    got = await client.call_tool("get_transcription", {"job_id": "job_test"})
    assert got.structured_content["status"] == "done"
    assert got.structured_content["text"] == "texto final"

    canc = await client.call_tool("cancel_transcription", {"job_id": "job_test"})
    assert canc.structured_content["cancelled"] is True


@pytest.mark.anyio
async def test_submit_without_source_is_error(client):
    res = await client.call_tool("submit_transcription", {})
    assert res.is_error is True
    assert "exactamente UNA" in res.content[0].text
