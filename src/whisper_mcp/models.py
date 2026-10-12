"""Modelos de salida de las tools.

El tipo de retorno de cada tool ES su `outputSchema`: el SDK lo publica en
`tools/list` y valida el valor devuelto contra él, rellenando también
`structured_content` (datos para la aplicación, no solo texto para el modelo).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Segment(BaseModel):
    start: float = Field(description="Inicio del segmento, en segundos.")
    end: float = Field(description="Fin del segmento, en segundos.")
    text: str = Field(description="Texto del segmento.")


class TranscribeResult(BaseModel):
    text: str = Field(description="Texto completo transcrito.")
    language: str | None = Field(default=None, description="Idioma, si el formato del core lo proporciona.")
    segments: list[Segment] = Field(default_factory=list, description="Segmentos con marcas de tiempo.")


class SubmitResult(BaseModel):
    job_id: str = Field(description="Identificador del trabajo en cola.")
    status: Literal["queued"] = Field(description="Estado inicial del trabajo.")
    accepted: bool = Field(description="True si el trabajo fue aceptado.")


class JobResult(BaseModel):
    status: Literal["queued", "processing", "done", "failed", "cancelled"] = Field(
        description="Estado del trabajo."
    )
    text: str | None = Field(default=None, description="Transcripción final, si está disponible.")
    segments: list[Segment] | None = Field(default=None, description="Segmentos, si están disponibles.")
    progress: float | None = Field(default=None, ge=0, le=1, description="Progreso de 0 a 1.")
    error: str | None = Field(default=None, description="Mensaje de error, si falló.")


class CancelResult(BaseModel):
    cancelled: bool = Field(description="True si se canceló.")
