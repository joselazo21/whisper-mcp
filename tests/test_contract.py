"""Tests de contrato: lo que `tools/list` y `resources/list` publican.

No llaman al core; validan el contrato MCP (nombres, schemas, annotations).
"""

from __future__ import annotations

import pytest
from mcp import Client

from whisper_mcp.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_tools_in_deterministic_order(client):
    listing = await client.list_tools()
    names = [t.name for t in listing.tools]
    assert names == ["transcribe", "submit_transcription", "get_transcription", "cancel_transcription"]


@pytest.mark.anyio
async def test_transcribe_input_schema_flat(client):
    listing = await client.list_tools()
    t = next(x for x in listing.tools if x.name == "transcribe")
    props = set(t.input_schema["properties"])
    assert props == {"audio_url", "audio_base64", "audio_path", "language", "prompt", "response_format"}
    assert "inp" not in props  # sin anidamiento


@pytest.mark.anyio
async def test_output_schema_present(client):
    listing = await client.list_tools()
    t = next(x for x in listing.tools if x.name == "transcribe")
    assert t.output_schema is not None
    assert set(t.output_schema["properties"]) >= {"text", "language", "segments"}


@pytest.mark.anyio
async def test_tool_annotations(client):
    listing = await client.list_tools()
    by_name = {t.name: t for t in listing.tools}
    assert by_name["get_transcription"].annotations.read_only_hint is True
    assert by_name["cancel_transcription"].annotations.idempotent_hint is True
    assert by_name["cancel_transcription"].annotations.destructive_hint is True


@pytest.mark.anyio
async def test_resource_mime_type(client):
    resources = await client.list_resources()
    r = next(x for x in resources.resources if x.uri == "whisper://info")
    assert r.mime_type == "application/json"


@pytest.mark.anyio
async def test_read_resource(client):
    result = await client.read_resource("whisper://info")
    assert result.contents  # no vacío
    import json

    data = json.loads(result.contents[0].text)
    assert data["engine"].startswith("faster-whisper")
    assert data["max_sync_seconds"] == 300
