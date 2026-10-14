"""Cliente HTTP hacia el core (mock en local; Speaches en producción).

Usa un único `httpx.AsyncClient` compartido, creado en el lifespan del servidor
(vía `init_client`) y cerrado al apagarlo (`close_client`). Evita crear un
cliente por llamada, como recomienda el SDK para recursos que deben sobrevivir
a un request.
"""

from __future__ import annotations

import os

import httpx

CORE_URL = os.getenv("WHISPER_CORE_URL", "http://127.0.0.1:8000").rstrip("/")
CORE_API_KEY = os.getenv("WHISPER_CORE_API_KEY", "")
DEFAULT_MODEL = os.getenv("WHISPER_MODEL_ID", "Systran/faster-whisper-large-v3")
SYNC_TIMEOUT_SECONDS = float(os.getenv("WHISPER_SYNC_TIMEOUT_SECONDS", "600"))

_client: httpx.AsyncClient | None = None


class CoreError(Exception):
    """Error devuelto por el core, con status HTTP para decidir el mensaje."""

    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.status = status


def init_client() -> httpx.AsyncClient:
    """Crea el cliente compartido. Se llama una vez, en el lifespan del servidor."""
    global _client
    _client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
    return _client


async def close_client() -> None:
    """Cierra el cliente compartido (al apagar el servidor)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("httpx client no inicializado; ¿falta el lifespan del servidor?")
    return _client


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    if CORE_API_KEY:
        headers["Authorization"] = f"Bearer {CORE_API_KEY}"
    return headers


def _raise_for(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    raise _map_error(response.status_code, response.text)


def _map_error(status: int, body: str) -> CoreError:
    if status in (401, 403):
        return CoreError("api_key inválida o ausente en el core (401/403).", status)
    if status == 404:
        return CoreError("recurso no encontrado en el core (404).", status)
    if status == 429:
        return CoreError("cuota excedida (429), reintenta más tarde.", status)
    if status >= 500:
        return CoreError(f"error interno del core ({status}).", status)
    return CoreError(f"el core respondió {status}: {body[:200]}", status)


async def transcribe_bytes(
    audio: bytes,
    filename: str,
    *,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
    response_format: str = "json",
    prompt: str | None = None,
    temperature: float | None = 0.0,
    timeout: float | None = None,
) -> dict:
    data: dict = {"model": model}
    if language:
        data["language"] = language
    if response_format:
        data["response_format"] = response_format
    if temperature is not None:
        data["temperature"] = str(temperature)
    if prompt:
        data["prompt"] = prompt

    response = await get_client().post(
        f"{CORE_URL}/v1/audio/transcriptions",
        data=data,
        files={"file": (filename, audio, "application/octet-stream")},
        headers=_headers(),
        timeout=httpx.Timeout(timeout or SYNC_TIMEOUT_SECONDS, connect=10.0),
    )
    _raise_for(response)
    # El mock devuelve JSON; Speaches puede devolver texto plano si
    # response_format="text". Devolver siempre un dict coherente.
    try:
        return response.json()
    except ValueError:
        return {"text": response.text}
