"""Entry point para el CLI de MCP (`mcp run`, `mcp dev`, `mcp install`).

El CLI carga este archivo como módulo suelto (no como parte del paquete
`whisper_mcp`), por lo que aquí se reexporta el servidor con un import absoluto
y se delega el arranque en `main()`.

Uso:
  mcp run server.py --transport streamable-http
  mcp dev server.py
  mcp install server.py --name "whisper-mcp"
"""

from whisper_mcp.server import main, mcp  # noqa: F401

if __name__ == "__main__":
    main()
