"""Validación y resolución del audio de entrada.

Resuelve `audio_url` / `audio_base64` / `audio_path` a bytes para enviarlos como
multipart al core y mide la duración decodificando el audio con FFmpeg.
"""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from math import isfinite

import imageio_ffmpeg

import httpx

MAX_BASE64_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_URL_BYTES = 25 * 1024 * 1024  # 25 MB al descargar
MAX_JOB_AUDIO_BYTES = 200 * 1024 * 1024  # trabajos largos, un worker a la vez
MAX_SYNC_SECONDS = 5 * 60
PROBE_TIMEOUT_SECONDS = 60


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


def estimate_duration(audio: bytes) -> float:
    """Decodifica la primera pista de audio y mide sus segundos reales.

    FFmpeg corre en un proceso aislado, sin acceso a protocolos de red y con
    límites de tiempo de proceso y de audio decodificado. Para clips largos
    basta con saber que superan el límite síncrono.
    """
    if not audio:
        raise AudioValidationError("El audio está vacío.")

    path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="whisper-duration-", suffix=".audio", delete=False) as source:
            path = source.name
            source.write(audio)

        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin", "-hide_banner", "-loglevel", "error",
            "-protocol_whitelist", "file,pipe",
            "-i", path,
            "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-t", str(MAX_SYNC_SECONDS + 1),
            "-f", "null", "-", "-progress", "pipe:1",
        ]
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise AudioValidationError("El archivo no contiene una pista de audio válida.")

        durations = [
            line.partition("=")[2]
            for line in result.stdout.decode("utf-8", errors="replace").splitlines()
            if line.startswith("out_time_us=")
        ]
        if not durations:
            raise AudioValidationError("No se pudo medir la duración del audio.")
        duration = int(durations[-1]) / 1_000_000
        if not isfinite(duration) or duration <= 0:
            raise AudioValidationError("El audio no tiene una duración válida.")
        return duration
    except subprocess.TimeoutExpired as exc:
        raise AudioValidationError("Se agotó el tiempo para validar el audio.") from exc
    except (OSError, ValueError) as exc:
        raise AudioValidationError("No se pudo validar la duración del audio.") from exc
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
