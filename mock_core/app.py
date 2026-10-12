"""Mock del core de Whisper.

Servicio HTTP compatible con OpenAI (mismo contrato que Speaches) que NO usa
ningún modelo real: devuelve transcripciones falsas. Sirve para desarrollar y
probar el servidor MCP sin CPU/GPU/Docker.

Endpoints:
  GET  /health                         -> liveness
  POST /v1/audio/transcriptions        -> transcripción falsa (multipart)

Forzar errores (para probar el mapeo core->MCP):
  Header  X-Mock-Status: <código>      -> responde ese código HTTP
  Header  X-Mock-Delay: <segundos>     -> retrasa la respuesta (timeout)

Auth (opcional): si se define MOCK_API_KEY, las rutas /v1/* exigen
Authorization: Bearer <MOCK_API_KEY>. /health queda abierto.

Arranque:
  python -m mock_core.app
  # o: uvicorn mock_core.app:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

import asyncio
import os
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile

app = FastAPI(title="whisper-mock-core", version="0.1.0")

MOCK_TEXT = "Esto es una transcripción de prueba (mock, sin modelo real)."


def _check_auth(authorization: str | None) -> None:
    key = os.getenv("MOCK_API_KEY", "")
    if not key:
        return
    if authorization != f"Bearer {key}":
        raise HTTPException(status_code=401, detail="api_key inválida o ausente")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default="Systran/faster-whisper-large-v3"),
    language: str | None = Form(default=None),
    response_format: str = Form(default="json"),
    temperature: float | None = Form(default=None),
    prompt: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
    x_mock_status: int | None = Header(default=None, alias="X-Mock-Status"),
    x_mock_delay: float | None = Header(default=None, alias="X-Mock-Delay"),
) -> dict:
    _check_auth(authorization)

    if x_mock_delay:
        await asyncio.sleep(x_mock_delay)

    if x_mock_status:
        raise HTTPException(status_code=x_mock_status, detail=f"respuesta forzada {x_mock_status}")

    # No decodificamos el audio: el mock solo lee su tamaño para simular que existe.
    _ = await file.read()

    return {
        "text": MOCK_TEXT,
        "language": language or "es",
        "duration": 10.0,
        "model": model,
        "segments": [
            {"id": 0, "start": 0.0, "end": 10.0, "text": MOCK_TEXT},
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MOCK_PORT", "8001")))
