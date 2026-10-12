# whisper-mcp (mock)

Servidor MCP de transcripción de audio + **mock del core**, para desarrollar y
probar el contrato **sin CPU, sin GPU y sin Docker**. El motor real
(faster-whisper vía Speaches) se conecta después cambiando una variable.

```text
cliente MCP ──► whisper_mcp (:8000, /mcp) ──► mock_core (:8001)  (respuestas falsas)
                                              └─ (producción: Speaches :8001)
```

Características:
- 4 tools + recurso `whisper://info`, con **salida estructurada** (`outputSchema` + `structured_content`).
- Parámetros planos, annotations (`readOnlyHint`, `idempotentHint`, `destructiveHint`).
- Cliente HTTP compartido (lifespan), errores accionables, validación de audio.
- Entry point compatible con el CLI de MCP (`mcp run` / `mcp dev` / `mcp install`).

## Requisitos

- Python 3.11+ (o `uv`, que instala Python por ti)

## Instalar

Con **uv** (recomendado; gestiona también la versión de Python):

```powershell
cd "C:\Users\Jose Morales\whisper-mcp"
uv python install 3.12
uv venv --python 3.12
uv pip install -e ".[dev]"
```

Con **pip** (si ya tienes Python 3.11+):

```bash
pip install -e ".[dev]"
```

## Ejecutar (dos terminales)

Terminal 1 — mock del core (puerto 8001):

```bash
.venv\Scripts\python.exe -m mock_core.app
```

Terminal 2 — servidor MCP (puerto 8000, Streamable HTTP en `/mcp`). Dos formas:

```bash
# A) directo (lee MCP_HOST / MCP_PORT / MCP_ALLOWED_* del entorno)
.venv\Scripts\python.exe -m whisper_mcp.server

# B) vía el CLI oficial (entry point en la raíz)
.venv\Scripts\mcp.exe run server.py --transport streamable-http
```

## Probar

### 1. Salud

```bash
curl http://127.0.0.1:8001/health       # mock core
curl http://127.0.0.1:8000/healthz      # MCP
curl http://127.0.0.1:8000/readyz       # MCP (comprueba el core)
```

### 2. Mock core directo (transcripción falsa)

```bash
curl -s http://127.0.0.1:8001/v1/audio/transcriptions \
  -F "model=Systran/faster-whisper-large-v3" \
  -F "file=@prueba-es.wav" \
  -F "language=es"
```

Forzar un error (para probar el mapeo core→MCP):

```bash
curl -s http://127.0.0.1:8001/v1/audio/transcriptions \
  -H "X-Mock-Status: 429" -F "file=@prueba-es.wav"
```

### 3. Suite de tests (pytest, in-memory)

```bash
.venv\Scripts\python.exe -m pytest -q
```

### 4. End-to-end con el cliente MCP

```bash
.venv\Scripts\python.exe scripts\smoke.py
```

### 5. MCP Inspector (interactivo)

```bash
npx @modelcontextprotocol/inspector
```

- Transport type: **Streamable HTTP**
- URL: `http://127.0.0.1:8000/mcp`

## Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `WHISPER_CORE_URL` | `http://127.0.0.1:8001` | URL del core (mock o Speaches) |
| `WHISPER_CORE_API_KEY` | *(vacío)* | Bearer reenviado al core |
| `WHISPER_MODEL_ID` | `Systran/faster-whisper-large-v3` | Modelo que se envía al core |
| `WHISPER_AUDIO_DIR` | `.` | Directorio permitido para `audio_path` |
| `MOCK_API_KEY` | *(vacío)* | Si se define, el mock exige Bearer |
| `MOCK_JOB_DELAY` | `1` | Segundos hasta que un job mock pasa a `done` |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8000` | Bind del servidor MCP |
| `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` | *(vacío)* | Listas (coma) para exponer el server fuera de localhost |
| `MCP_LOG_LEVEL` | `INFO` | Nivel de logging |
| `MCP_DEBUG` | `false` | Activa debug del SDK |

## Qué está en stub (a completar después)

- `audio.estimate_duration()`: devuelve duración fija; en producción usar ffprobe.
- Auth del MCP (Bearer por cliente): pendiente (fase 6 del plan).
- Webhook del core: el mock no dispara el POST firmado.

## Pasar a producción

1. Cambia `WHISPER_CORE_URL` al contenedor Speaches (`http://127.0.0.1:8001`).
2. Define `WHISPER_CORE_API_KEY` (misma clave que `API_KEY` de Speaches).
3. Sustituye `estimate_duration` por ffprobe y añade auth en el MCP.
