"""Tests unitarios de la validación de audio (sin servidor, sin HTTP)."""

from __future__ import annotations

import base64

import pytest

from whisper_mcp.audio import AudioValidationError, estimate_duration, resolve_audio_bytes


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_reject_no_source():
    with pytest.raises(AudioValidationError):
        await resolve_audio_bytes(None, None, None)


@pytest.mark.anyio
async def test_reject_multiple_sources():
    with pytest.raises(AudioValidationError):
        await resolve_audio_bytes("http://example.com/a.wav", "aGVsbG8=", None)


@pytest.mark.anyio
async def test_reject_invalid_base64():
    with pytest.raises(AudioValidationError):
        await resolve_audio_bytes(None, "¡¡no-es-base64!!", None)


@pytest.mark.anyio
async def test_base64_ok():
    raw, name = await resolve_audio_bytes(None, base64.b64encode(b"hello").decode(), None)
    assert raw == b"hello"
    assert name == "audio.bin"


@pytest.mark.anyio
async def test_reject_oversized_base64():
    big = base64.b64encode(b"x" * (5 * 1024 * 1024 + 1)).decode()
    with pytest.raises(AudioValidationError):
        await resolve_audio_bytes(None, big, None)


@pytest.mark.anyio
async def test_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setenv("WHISPER_AUDIO_DIR", str(tmp_path))
    with pytest.raises(AudioValidationError):
        await resolve_audio_bytes(None, None, "../secret.wav")


@pytest.mark.anyio
async def test_path_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("WHISPER_AUDIO_DIR", str(tmp_path))
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF....")
    raw, name = await resolve_audio_bytes(None, None, str(p))
    assert raw == b"RIFF...."
    assert name == "a.wav"


@pytest.mark.anyio
async def test_url_scheme_restricted():
    # Solo http/https (anti-SSRF).
    with pytest.raises(AudioValidationError):
        await resolve_audio_bytes("ftp://example.com/a.wav", None, None)


def test_estimate_duration_is_stub():
    assert estimate_duration(b"cualquier-cosa") == 10.0
