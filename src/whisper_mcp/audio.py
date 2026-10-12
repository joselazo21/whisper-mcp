"""Validación y resolución del audio de entrada.

Resuelve `audio_url` / `audio_base64` / `audio_path` a bytes para enviarlos como
multipart al core. La estimación de duración real está en *stub* a propósito:
el mock no decodifica audio (no usa ffmpeg/CPU). Para producción, sustituir
`estimate_duration` por ffprobe o similar.
"""

from __future__ import annotations

import base64
import os

import httpx

MAX_BASE64_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_URL_BYTES = 25 * 1024 * 1024  # 25 MB al descargar
MAX_JOB_AUDIO_BYTES = 200 * 1024 * 1024  # trabajos largos, un worker a la vez


class AudioValidationError(Exception):
    """Error de negocio sobre la fuente de audio (se mapea a isError en MCP)."""


def _allowed_dir() -> str:
    return os.path.abspath(os.getenv("WHISPER_AUDIO_DIR", "."))


async def resolve_audio_bytes(
    audio_url: str | None,
    audio_base64: str | None,
    audio_path: str | None,
    *,
    max_bytes: int = MAX_URL_BYTES,
) -> tuple[bytes, str]:
    """Devuelve (bytes, filename). Exige exactamente una fuente."""
    sources = [s for s in (audio_url, audio_base64, audio_path) if s]
    if len(sources) != 1:
        raise AudioValidationError(
            "Proporciona exactamente UNA de audio_url, audio_base64 o audio_path."
        )

    if audio_base64 is not None:
        try:
            raw = base64.b64decode(audio_base64, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise AudioValidationError("audio_base64 no es base64 válido.") from exc
        if len(raw) > MAX_BASE64_BYTES:
            raise AudioValidationError("audio_base64 excede 5 MB; usa audio_url o audio_path.")
        return raw, "audio.bin"

    if audio_path is not None:
        base = os.path.realpath(_allowed_dir())
        full = os.path.realpath(os.path.join(base, audio_path))
        try:
            inside = os.path.commonpath((base, full)) == base
        except ValueError:
            inside = False
        if not inside:
            raise AudioValidationError("audio_path fuera del directorio permitido.")
        try:
            with open(full, "rb") as f:
                raw = f.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise AudioValidationError(f"audio_path excede {max_bytes // (1024 * 1024)} MB.")
            return raw, os.path.basename(audio_path)
        except OSError as exc:  # noqa: BLE001
            raise AudioValidationError(f"No se pudo leer audio_path: {exc}") from exc

    # audio_url
    if not audio_url.startswith(("http://", "https://")):
        raise AudioValidationError("audio_url debe ser http/https.")
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            chunks = []
            total = 0
            async with client.stream("GET", audio_url) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise AudioValidationError(f"audio_url excede {max_bytes // (1024 * 1024)} MB.")
                    chunks.append(chunk)
            raw = b"".join(chunks)
    except httpx.HTTPError as exc:
        raise AudioValidationError(f"No se pudo descargar audio_url: {exc}") from exc

    return raw, "audio.bin"


def estimate_duration(_audio: bytes) -> float:
    """STUB: devuelve una duración fija. No decodifica audio.

    En producción, usar ffprobe u otra librería para obtener la duración real y
    rechazar clips > 5 minutos en la tool `transcribe` (no fiarse del tamaño).
    """
    return 10.0
