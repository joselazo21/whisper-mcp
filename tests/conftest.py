"""Fixtures compartidas: cliente MCP in-memory (sin subproceso, sin puerto).

El `Client` del SDK también conecta en memoria pasándole el objeto servidor,
igual que el `TestClient` de FastAPI. Así los tests de contrato y de tools no
dependen de servidores levantados.
"""

from __future__ import annotations

import pytest
from mcp import Client

from whisper_mcp.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        yield c
