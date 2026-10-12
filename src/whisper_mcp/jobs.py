"""Estado durable de trabajos y outbox transaccional en PostgreSQL."""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://whisper:whisper@127.0.0.1:5432/whisper")
MAX_ATTEMPTS = 3
LEASE_SECONDS = 300


class JobNotFound(Exception):
    pass


def _job_uuid(job_id: str) -> UUID:
    try:
        return UUID(job_id)
    except (ValueError, AttributeError) as exc:
        raise JobNotFound("job_id no encontrado.") from exc


async def ping() -> None:
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        await conn.execute("SELECT 1")


async def submit_job(*, audio_url: str | None, audio_path: str | None, language: str | None) -> dict:
    job_id = uuid4()
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        await conn.execute(
            """INSERT INTO transcription_jobs (id, status, audio_url, audio_path, language)
               VALUES (%s, 'queued', %s, %s, %s)""",
            (job_id, audio_url, audio_path, language),
        )
        await conn.execute("INSERT INTO job_outbox (job_id) VALUES (%s)", (job_id,))
    return {"job_id": str(job_id), "status": "queued", "accepted": True}


async def get_job(job_id: str) -> dict:
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row) as conn:
        row = await (
            await conn.execute(
                """SELECT status, text, segments, progress, error
                   FROM transcription_jobs WHERE id = %s""",
                (_job_uuid(job_id),),
            )
        ).fetchone()
    if row is None:
        raise JobNotFound("job_id no encontrado.")
    return row


async def cancel_job(job_id: str) -> dict:
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        row = await (
            await conn.execute(
                """UPDATE transcription_jobs
                   SET status = 'cancelled', lease_token = NULL, lease_expires_at = NULL,
                       updated_at = now(), finished_at = now()
                   WHERE id = %s AND status IN ('queued', 'processing')
                   RETURNING id""",
                (_job_uuid(job_id),),
            )
        ).fetchone()
        if row is not None:
            return {"cancelled": True}
        existing = await (
            await conn.execute("SELECT status FROM transcription_jobs WHERE id = %s", (_job_uuid(job_id),))
        ).fetchone()
    if existing is None:
        raise JobNotFound("job_id no encontrado.")
    return {"cancelled": existing[0] == "cancelled"}


async def claim_job(job_id: str) -> dict | None:
    token = uuid4()
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row) as conn:
        row = await (
            await conn.execute(
                """UPDATE transcription_jobs
                   SET status = 'processing', attempts = attempts + 1,
                       lease_token = %s,
                       lease_expires_at = now() + (%s * interval '1 second'),
                       updated_at = now(), error = NULL
                   WHERE id = %s AND status = 'queued' AND attempts < %s
                   RETURNING id, audio_url, audio_path, language, attempts""",
                (token, LEASE_SECONDS, _job_uuid(job_id), MAX_ATTEMPTS),
            )
        ).fetchone()
    if row is not None:
        row["lease_token"] = token
    return row


async def renew_lease(job_id: UUID, token: UUID) -> bool:
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        row = await (
            await conn.execute(
                """UPDATE transcription_jobs
                   SET lease_expires_at = now() + (%s * interval '1 second'), updated_at = now()
                   WHERE id = %s AND lease_token = %s AND status = 'processing'
                   RETURNING id""",
                (LEASE_SECONDS, job_id, token),
            )
        ).fetchone()
    return row is not None


async def finish_job(job_id: UUID, token: UUID, result: dict) -> bool:
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        row = await (
            await conn.execute(
                """UPDATE transcription_jobs
                   SET status = 'done', text = %s, segments = %s, progress = 1,
                       error = NULL, lease_token = NULL, lease_expires_at = NULL,
                       updated_at = now(), finished_at = now()
                   WHERE id = %s AND lease_token = %s AND status = 'processing'
                   RETURNING id""",
                (result["text"], Jsonb(result.get("segments") or []), job_id, token),
            )
        ).fetchone()
    return row is not None


async def fail_job(job_id: UUID, token: UUID, error: str, retryable: bool) -> None:
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        row = await (
            await conn.execute(
                """UPDATE transcription_jobs
                   SET status = CASE WHEN %s AND attempts < %s THEN 'queued' ELSE 'failed' END,
                       error = %s, lease_token = NULL, lease_expires_at = NULL,
                       updated_at = now(),
                       finished_at = CASE WHEN %s AND attempts < %s THEN NULL ELSE now() END
                   WHERE id = %s AND lease_token = %s AND status = 'processing'
                   RETURNING status, attempts""",
                (retryable, MAX_ATTEMPTS, error[:500], retryable, MAX_ATTEMPTS, job_id, token),
            )
        ).fetchone()
        if row is not None and row[0] == "queued":
            delay = min(30 * 2 ** (row[1] - 1), 300)
            await conn.execute(
                """INSERT INTO job_outbox (job_id, available_at)
                   VALUES (%s, now() + (%s * interval '1 second'))""",
                (job_id, delay),
            )


async def recover_expired_jobs() -> int:
    """Reencola trabajos cuyo worker dejó de renovar su lease."""
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        rows = await (
            await conn.execute(
                """UPDATE transcription_jobs
                   SET status = CASE WHEN attempts < %s THEN 'queued' ELSE 'failed' END,
                       error = 'El worker dejó de responder.',
                       lease_token = NULL, lease_expires_at = NULL, updated_at = now(),
                       finished_at = CASE WHEN attempts < %s THEN NULL ELSE now() END
                   WHERE status = 'processing' AND lease_expires_at < now()
                   RETURNING id, status""",
                (MAX_ATTEMPTS, MAX_ATTEMPTS),
            )
        ).fetchall()
        for job_id, status in rows:
            if status == "queued":
                await conn.execute("INSERT INTO job_outbox (job_id) VALUES (%s)", (job_id,))
    return len(rows)
