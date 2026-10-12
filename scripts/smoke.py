"""Smoke test completo del servidor MCP (mock).

Requiere mock core (:8001) y MCP (:8000) corriendo.
  python scripts/smoke.py

Cubre: list_tools, transcribe, error sin fuente, error job inexistente,
flujo asíncrono (submit -> get -> get tras delay -> cancel) y el recurso.
"""

from __future__ import annotations

import asyncio
import json

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
    async with Client(MCP_URL) as client:
        listing = await client.list_tools()
        tools = [getattr(t, "name", t) for t in getattr(listing, "tools", listing)]
        print("TOOLS:", tools)

        r = await client.call_tool("transcribe", {"audio_base64": "aGVsbG8=", "language": "es"})
        print("\n[transcribe]            isError =", _is_error(r))
        print("                       ", _texts(r)[0])

        r = await client.call_tool("transcribe", {})
        print("\n[transcribe s/ fuente]  isError =", _is_error(r))
        print("                       ", _texts(r)[0])

        r = await client.call_tool("get_transcription", {"job_id": "job_noexiste"})
        print("\n[job inexistente]       isError =", _is_error(r))
        print("                       ", _texts(r)[0])

        r = await client.call_tool(
            "submit_transcription", {"audio_url": "http://example.com/a.wav", "language": "es"}
        )
        job = json.loads(_texts(r)[0])
        jid = job["job_id"]
        print("\n[submit]                ->", job)

        r = await client.call_tool("get_transcription", {"job_id": jid})
        print("[get inmediato]         ->", _texts(r)[0])

        await asyncio.sleep(1.5)
        r = await client.call_tool("get_transcription", {"job_id": jid})
        print("[get tras 1.5s]         ->", _texts(r)[0])

        r = await client.call_tool("cancel_transcription", {"job_id": jid})
        print("[cancel]                ->", _texts(r)[0])

        r = await client.read_resource("whisper://info")
        print("\n[recurso whisper://info]", _texts(r)[0])


if __name__ == "__main__":
    asyncio.run(main())
