"""Comprueba el procesamiento y la política de reintentos sin servicios externos."""

from __future__ import annotations

from uuid import uuid4

import pytest

from whisper_mcp import core_client, jobs, worker
from whisper_mcp.core_client import CoreError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _job() -> dict:
    return {
        "id": uuid4(),
        "lease_token": uuid4(),
        "audio_url": None,
        "audio_path": "meeting.wav",
        "language": "es",
    }


@pytest.mark.anyio
async def test_worker_saves_real_core_result(monkeypatch):
    saved = []

    async def fake_audio(*args, **kwargs):
        return b"audio", "meeting.wav"

    async def fake_core(audio, filename, **kwargs):
        assert kwargs["response_format"] == "verbose_json"
        return {"text": "reunión transcrita", "segments": [{"start": 0, "end": 1, "text": "reunión"}]}

    async def fake_finish(job_id, token, result):
        saved.append(result)
        return True

    monkeypatch.setattr(worker, "resolve_audio_bytes", fake_audio)
    monkeypatch.setattr(core_client, "transcribe_bytes", fake_core)
    monkeypatch.setattr(jobs, "finish_job", fake_finish)

    await worker.process_job(_job())
    assert saved[0]["text"] == "reunión transcrita"


@pytest.mark.anyio
async def test_worker_retries_core_503(monkeypatch):
    failures = []

    async def fake_audio(*args, **kwargs):
        return b"audio", "meeting.wav"

    async def fake_core(*args, **kwargs):
        raise CoreError("core no disponible", 503)

    async def fake_fail(job_id, token, error, retryable):
        failures.append((error, retryable))

    monkeypatch.setattr(worker, "resolve_audio_bytes", fake_audio)
    monkeypatch.setattr(core_client, "transcribe_bytes", fake_core)
    monkeypatch.setattr(jobs, "fail_job", fake_fail)

    await worker.process_job(_job())
    assert failures == [("core no disponible", True)]
