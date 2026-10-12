"""Fixtures compartidas: cliente MCP en memoria y muestras de audio real."""

from __future__ import annotations

import base64
import io
import wave

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


def wav_bytes(seconds: float, sample_rate: int = 1000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x80" * round(seconds * sample_rate))
    return output.getvalue()


@pytest.fixture
def short_wav_base64() -> str:
    return base64.b64encode(wav_bytes(1)).decode("ascii")
