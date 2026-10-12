"""Smoke test completo del servidor MCP (mock).

Requiere el stack de compose (PostgreSQL, RabbitMQ, worker, mock core y MCP).
  python scripts/smoke.py

Cubre: list_tools, transcribe, error sin fuente, error job inexistente,
flujo real de cola (submit -> worker -> get) y el recurso.
"""

from __future__ import annotations

import asyncio
import base64
import json
import wave
from pathlib import Path

from mcp import Client

MCP_URL = "http://127.0.0.1:8000/mcp"


def _texts(result: object) -> list[str]:
    content = getattr(result, "content", None)
    if content is None:
        content = getattr(result, "contents", None)
    if content is None:
        return [repr(result)]
    return [getattr(item, "text", str(item)) for item in content]


def _is_error(result: object) -> bool:
    return bool(getattr(result, "is_error", getattr(result, "isError", False)))


async def main() -> None:
    audio_dir = Path(__file__).resolve().parents[1] / "audio"
    audio_dir.mkdir(exist_ok=True)
    with wave.open(str(audio_dir / "smoke.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00" * 32000)

    async with Client(MCP_URL) as client:
        listing = await client.list_tools()
        tools = [getattr(t, "name", t) for t in getattr(listing, "tools", listing)]
        print("TOOLS:", tools)

        sample = base64.b64encode((audio_dir / "smoke.wav").read_bytes()).decode("ascii")
        r = await client.call_tool("transcribe", {"audio_base64": sample, "language": "es"})
        print("\n[transcribe]            isError =", _is_error(r))
        print("                       ", _texts(r)[0])

        r = await client.call_tool("transcribe", {})
        print("\n[transcribe s/ fuente]  isError =", _is_error(r))
        print("                       ", _texts(r)[0])

        r = await client.call_tool("get_transcription", {"job_id": "job_noexiste"})
        print("\n[job inexistente]       isError =", _is_error(r))
        print("                       ", _texts(r)[0])

        r = await client.call_tool(
            "submit_transcription", {"audio_path": "smoke.wav", "language": "es"}
        )
        job = json.loads(_texts(r)[0])
        jid = job["job_id"]
        print("\n[submit]                ->", job)

        for _ in range(30):
            r = await client.call_tool("get_transcription", {"job_id": jid})
            state = json.loads(_texts(r)[0])
            if state["status"] in ("done", "failed", "cancelled"):
                break
            await asyncio.sleep(1)
        print("[resultado del worker] ->", state)
        assert state["status"] == "done", state

        r = await client.read_resource("whisper://info")
        print("\n[recurso whisper://info]", _texts(r)[0])


if __name__ == "__main__":
    asyncio.run(main())
