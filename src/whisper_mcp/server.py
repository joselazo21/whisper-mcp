"""Servidor MCP de Whisper (adaptador delgado, sin GPU, sin modelo).

Expone 4 tools + un recurso (`whisper://info`). La transcripción síncrona va
al core (mock en local, Speaches en producción); los trabajos largos se
persisten en PostgreSQL y los procesa el worker vía RabbitMQ.

- Parámetros PLANOS (inputSchema fiel al contrato).
- Salida estructurada (outputSchema + structured_content) vía tipos de retorno.
- Cliente HTTP compartido creado en el lifespan.
- Errores de negocio como `ToolError` (isError:true con mensaje claro).

Arranque:
  python -m whisper_mcp.server                           # directo
  mcp run server.py --transport streamable-http          # vía CLI (entry point en la raíz)
"""

from __future__ import annotations

import json
import os
import sys
import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from functools import wraps
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import core_client, jobs
from .audio import AudioValidationError, estimate_duration, resolve_audio_bytes
from .core_client import CoreError
from .models import CancelResult, JobResult, SubmitResult, TranscribeResult

MAX_SYNC_SECONDS = 5 * 60


@asynccontextmanager
async def _lifespan(server: MCPServer):
    """Crea el cliente HTTP compartido al arrancar y lo cierra al apagar."""
    core_client.init_client()
    try:
        yield
    finally:
        await core_client.close_client()


mcp = MCPServer(
    name="whisper-mcp",
    title="Whisper (transcripción de voz)",
    description="Transcripción de audio a texto vía faster-whisper, expuesta como API MCP.",
    instructions=(
        "Usa 'transcribe' para clips de hasta 5 minutos y "
        "'submit_transcription' + 'get_transcription' para audios largos. "
        "Proporciona exactamente UNA fuente de audio."
    ),
    version="0.1.0",
    # Dependencias de runtime que `mcp dev`/`mcp install` instalan por sí solas.
    dependencies=["httpx", "psycopg[binary]", "aio-pika"],
    log_level=os.getenv("MCP_LOG_LEVEL", "INFO").upper(),
    debug=os.getenv("MCP_DEBUG", "false").lower() in ("1", "true", "yes", "on"),
    lifespan=_lifespan,
)


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False)


def _as_tool_error(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Traduce errores de negocio a ToolError (mensaje claro, isError: true)."""

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except (AudioValidationError, CoreError, jobs.JobNotFound) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


# --------------------------------------------------------------------------- #
# Recurso
# --------------------------------------------------------------------------- #
@mcp.resource(
    "whisper://info",
    name="info",
    description="Metadatos del servicio (modelo, idiomas, límites).",
    mime_type="application/json",
)
def info() -> str:
    return _json(
        {
            "model": core_client.DEFAULT_MODEL,
            "languages": ["es", "en", "auto"],
            "engine": "faster-whisper-compatible core",
            "max_sync_seconds": MAX_SYNC_SECONDS,
            "core_url": core_client.CORE_URL,
        }
    )


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="transcribe",
    description=(
        "Transcribe un clip de audio de hasta 5 minutos a texto. "
        "Para audio más largo usa submit_transcription. "
        "Proporciona exactamente UNA de audio_url, audio_base64 o audio_path."
    ),
)
@_as_tool_error
async def transcribe(
    audio_url: Annotated[
        str | None, Field(description="URL accesible por el servidor (http/https). Recomendado para producción.")
    ] = None,
    audio_base64: Annotated[
        str | None, Field(description="Contenido del audio en base64. Solo clips pequeños (hasta 5 MB).")
    ] = None,
    audio_path: Annotated[
        str | None, Field(description="Ruta en un volumen compartido con el servidor.")
    ] = None,
    language: Annotated[str | None, Field(description="Código ISO-639-1, p. ej. 'es'. Opcional.")] = None,
    prompt: Annotated[
        str | None, Field(description="Contexto/ortografía a sesgar (nombres, cifras, jerga del cliente).")
    ] = None,
    response_format: Annotated[
        Literal["json", "text", "verbose_json"], Field(description="Formato de la respuesta.")
    ] = "json",
) -> TranscribeResult:
    raw, filename = await resolve_audio_bytes(audio_url, audio_base64, audio_path)

    # Validación de duración (stub en el mock: ver audio.estimate_duration).
    duration = estimate_duration(raw)
    if duration > MAX_SYNC_SECONDS:
        raise AudioValidationError(
            f"El audio dura ~{duration:.0f}s y supera el límite de 5 minutos; "
            "usa submit_transcription para audio largo."
        )

    result = await core_client.transcribe_bytes(
        raw,
        filename,
        model=core_client.DEFAULT_MODEL,
        language=language,
        response_format=response_format,
        prompt=prompt,
        temperature=0.0,
    )
    return TranscribeResult.model_validate(result)


@mcp.tool(
    name="submit_transcription",
    description=(
        "Encola una transcripción de audio largo (llamadas/reuniones). "
        "Devuelve un job_id para consultar estado con get_transcription. "
        "Proporciona exactamente UNA de audio_url o audio_path."
    ),
)
@_as_tool_error
async def submit_transcription(
    audio_url: Annotated[str | None, Field(description="URL accesible por el servidor (http/https).")] = None,
    audio_path: Annotated[str | None, Field(description="Ruta en un volumen compartido con el servidor.")] = None,
    language: Annotated[str | None, Field(description="Código ISO-639-1. Opcional.")] = None,
    webhook_url: Annotated[
        str | None, Field(description="Reservado para una futura entrega por webhook; no disponible aún.")
    ] = None,
) -> SubmitResult:
    sources = [s for s in (audio_url, audio_path) if s]
    if len(sources) != 1:
        raise AudioValidationError("Proporciona exactamente UNA de audio_url o audio_path.")
    if webhook_url is not None:
        raise AudioValidationError("webhook_url todavía no está disponible; consulta el resultado con get_transcription.")
    if audio_url is not None and not audio_url.startswith(("http://", "https://")):
        raise AudioValidationError("audio_url debe ser http/https.")

    result = await jobs.submit_job(audio_url=audio_url, audio_path=audio_path, language=language)
    return SubmitResult.model_validate(result)


@mcp.tool(
    name="get_transcription",
    description="Consulta el estado de un trabajo de transcripción (queued/processing/done/failed/cancelled).",
    annotations=ToolAnnotations(read_only_hint=True),
)
@_as_tool_error
async def get_transcription(
    job_id: Annotated[str, Field(description="Identificador del trabajo devuelto por submit_transcription.")],
) -> JobResult:
    result = await jobs.get_job(job_id)
    return JobResult.model_validate(result)


@mcp.tool(
    name="cancel_transcription",
    description="Cancela un trabajo en cola o en proceso. Idempotente.",
    annotations=ToolAnnotations(idempotent_hint=True, destructive_hint=True),
)
@_as_tool_error
async def cancel_transcription(
    job_id: Annotated[str, Field(description="Identificador del trabajo a cancelar.")],
) -> CancelResult:
    result = await jobs.cancel_job(job_id)
    return CancelResult.model_validate(result)


# --------------------------------------------------------------------------- #
# Rutas de salud (públicas)
# --------------------------------------------------------------------------- #
@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(request: Request) -> JSONResponse:
    # El MCP necesita el core para la tool síncrona y PostgreSQL para los jobs.
    try:
        response = await core_client.get_client().get(f"{core_client.CORE_URL}/health", timeout=3.0)
        response.raise_for_status()
        await jobs.ping()
        return JSONResponse({"status": "ready"})
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "not_ready"}, status_code=503)


# --------------------------------------------------------------------------- #
# Arranque
# --------------------------------------------------------------------------- #
def _transport_security(host: str) -> TransportSecuritySettings | None:
    """Protección DNS-rebinding.

    El SDK la auto-habilita solo cuando `host` es localhost. Al exponer el
    servidor en `0.0.0.0` (contenedor/remoto), hay que definir explícitamente
    los orígenes/hosts permitidos vía MCP_ALLOWED_ORIGINS / MCP_ALLOWED_HOSTS.
    """
    allowed_hosts = [h for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h]
    allowed_origins = [o for o in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if o]
    if allowed_hosts or allowed_origins:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )
    return None


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    host = os.getenv("MCP_HOST", "127.0.0.1")
    mcp.run(
        transport="streamable-http",
        host=host,
        port=int(os.getenv("MCP_PORT", "8000")),
        streamable_http_path="/mcp",
        transport_security=_transport_security(host),
    )


if __name__ == "__main__":
    main()
