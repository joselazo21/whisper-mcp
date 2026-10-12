"""Publicador de outbox y worker de transcripciones largas."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from uuid import UUID

import aio_pika
import httpx
import psycopg

from . import core_client, jobs
from .audio import AudioValidationError, MAX_JOB_AUDIO_BYTES, resolve_audio_bytes
from .core_client import CoreError

logger = logging.getLogger(__name__)
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://whisper:whisper@127.0.0.1/")
QUEUE_NAME = os.getenv("WHISPER_JOB_QUEUE", "whisper.transcriptions")
JOB_TIMEOUT_SECONDS = float(os.getenv("WHISPER_JOB_TIMEOUT_SECONDS", "1200"))


async def _publish_one(channel) -> bool:
    """La fila se borra solo tras la confirmación del broker."""
    async with await psycopg.AsyncConnection.connect(jobs.DATABASE_URL) as conn:
        row = await (
            await conn.execute(
                """SELECT id, job_id FROM job_outbox
                   WHERE available_at <= now() ORDER BY id
                   FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
        ).fetchone()
        if row is None:
            return False
        outbox_id, job_id = row
        confirmed = await channel.default_exchange.publish(
            aio_pika.Message(
                body=str(job_id).encode("ascii"),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=str(outbox_id),
            ),
            routing_key=QUEUE_NAME,
            mandatory=True,
        )
        if confirmed is False:
            raise RuntimeError("RabbitMQ rechazó el mensaje")
        await conn.execute("DELETE FROM job_outbox WHERE id = %s", (outbox_id,))
    return True


async def publish_loop() -> None:
    while True:
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL)
            async with connection:
                channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
                await channel.declare_queue(
                    QUEUE_NAME, durable=True, arguments={"x-queue-type": "quorum"}
                )
                while True:
                    await jobs.recover_expired_jobs()
                    if not await _publish_one(channel):
                        await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Falló el publicador; reintentando")
            await asyncio.sleep(5)


async def _renew_until_done(job_id: UUID, token: UUID) -> None:
    while True:
        await asyncio.sleep(60)
        if not await jobs.renew_lease(job_id, token):
            return


async def process_job(job: dict) -> None:
    job_id = job["id"]
    token = job["lease_token"]
    heartbeat = asyncio.create_task(_renew_until_done(job_id, token))
    try:
        raw, filename = await resolve_audio_bytes(
            job["audio_url"], None, job["audio_path"], max_bytes=MAX_JOB_AUDIO_BYTES
        )
        result = await core_client.transcribe_bytes(
            raw,
            filename,
            model=core_client.DEFAULT_MODEL,
            language=job["language"],
            response_format="verbose_json",
            timeout=JOB_TIMEOUT_SECONDS,
        )
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise ValueError("Respuesta de transcripción inválida del core")
        if not await jobs.finish_job(job_id, token, result):
            logger.info("Se descartó el resultado de %s: trabajo cancelado o lease perdido", job_id)
    except (AudioValidationError, ValueError) as exc:
        await jobs.fail_job(job_id, token, str(exc), retryable=False)
    except CoreError as exc:
        await jobs.fail_job(job_id, token, str(exc), retryable=exc.status == 429 or exc.status >= 500)
    except (httpx.RequestError, TimeoutError) as exc:
        await jobs.fail_job(job_id, token, str(exc), retryable=True)
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


async def consume_loop() -> None:
    core_client.init_client()
    try:
        while True:
            try:
                connection = await aio_pika.connect_robust(RABBITMQ_URL)
                async with connection:
                    channel = await connection.channel()
                    await channel.set_qos(prefetch_count=1)
                    queue = await channel.declare_queue(
                        QUEUE_NAME, durable=True, arguments={"x-queue-type": "quorum"}
                    )
                    async with queue.iterator() as messages:
                        async for message in messages:
                            try:
                                job_id = str(UUID(message.body.decode("ascii")))
                            except (ValueError, UnicodeError):
                                logger.error("Mensaje con job_id inválido: %r", message.body[:100])
                                await message.reject(requeue=False)
                                continue
                            try:
                                job = await jobs.claim_job(job_id)
                            except Exception:
                                logger.exception("No se pudo reclamar el trabajo %s", job_id)
                                await message.reject(requeue=True)
                                await asyncio.sleep(5)
                                continue
                            # PostgreSQL conserva el estado. Si el worker cae tras el ack,
                            # el lease vence y el publicador vuelve a encolar el trabajo.
                            await message.ack()
                            if job is not None:
                                try:
                                    await process_job(job)
                                except Exception:
                                    logger.exception("Error inesperado en %s; se recuperará al vencer el lease", job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Falló el consumidor; reconectando")
                await asyncio.sleep(5)
    finally:
        await core_client.close_client()


async def run() -> None:
    await asyncio.gather(publish_loop(), consume_loop())


def main() -> None:
    logging.basicConfig(level=os.getenv("MCP_LOG_LEVEL", "INFO").upper())
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())


if __name__ == "__main__":
    main()
