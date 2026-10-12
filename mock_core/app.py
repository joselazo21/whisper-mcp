"""Mock del core de Whisper.

Servicio HTTP compatible con OpenAI (mismo contrato que Speaches) que NO usa
ningún modelo real: devuelve transcripciones falsas. Sirve para desarrollar y
probar el servidor MCP sin CPU/GPU/Docker.

Endpoints:
  GET  /health                         -> liveness
  POST /v1/audio/transcriptions        -> transcripción falsa (multipart)
  POST /v1/jobs                        -> encolar transcripción larga (JSON)
  GET  /v1/jobs/{job_id}               -> estado
  DELETE /v1/jobs/{job_id}             -> cancelar

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
import uuid

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile

app = FastAPI(title="whisper-mock-core", version="0.1.0")

JOBS: dict[str, dict] = {}

MOCK_TEXT = "Esto es una transcripción de prueba (mock, sin modelo real)."


def _check_auth(authorization: str | None) -> None:
    key = os.getenv("MOCK_API_KEY", "")
    if not key:
        return
    if authorization != f"Bearer {key}":
        raise HTTPException(status_code=401, detail="api_key inválida o ausente")


async def _finish_job(job_id: str) -> None:
    delay = float(os.getenv("MOCK_JOB_DELAY", "1"))
    await asyncio.sleep(delay)
    job = JOBS.get(job_id)
    if job and job["status"] == "queued":
        job["status"] = "done"
        job["text"] = "Transcripción asíncrona de prueba (mock)."
        job["segments"] = [{"start": 0.0, "end": 10.0, "text": "Transcripción asíncrona de prueba (mock)."}]


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


@app.post("/v1/jobs")
async def submit_job(
    payload: dict = Body(...),
    authorization: str | None = Header(default=None),
) -> dict:
    _check_auth(authorization)

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    JOBS[job_id] = {
        "status": "queued",
        "audio": payload,
        "text": None,
        "segments": None,
    }
    asyncio.create_task(_finish_job(job_id))
    return {"job_id": job_id, "status": "queued", "accepted": True}


@app.get("/v1/jobs/{job_id}")
async def get_job(job_id: str, authorization: str | None = Header(default=None)) -> dict:
    _check_auth(authorization)
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_id no encontrado")
    done = job["status"] == "done"
    return {
        "status": job["status"],
        "text": job["text"],
        "segments": job["segments"],
        "progress": 1.0 if done else 0.0,
    }


@app.delete("/v1/jobs/{job_id}")
async def cancel_job(job_id: str, authorization: str | None = Header(default=None)) -> dict:
    _check_auth(authorization)
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_id no encontrado")
    job["status"] = "cancelled"
    return {"cancelled": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MOCK_PORT", "8001")))
