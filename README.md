# Whisper MCP

Servidor MCP de transcripción. `transcribe` llama directamente al core; los
trabajos largos usan PostgreSQL para estado y resultados, RabbitMQ para avisar
al worker y una tabla outbox para no perder el aviso entre ambos sistemas.
El contrato y las decisiones están en [diseño](docs/Whisper-MCP-diseno.md) y
[buenas prácticas](docs/Whisper-MCP-buenas-practicas.md).

```text
cliente MCP -> whisper-mcp -> PostgreSQL (trabajo + outbox)
                                  |                 |
                                  |                 +-> RabbitMQ -> worker
                                  |                                  |
                                  +<---- resultado --+----> Speaches
```

RabbitMQ transporta únicamente el `job_id`; el audio vive en un volumen
compartido o en una URL accesible para el worker. El worker llama a la API
`/v1/audio/transcriptions` de Speaches, no a una API `/v1/jobs` inexistente.
La lectura de audio se corta a 25 MB en `transcribe` y a 200 MB en el worker.
En formatos `json` o `text`, Speaches puede omitir el idioma y los segmentos;
el MCP devuelve `language: null` y `segments: []` en ese caso.

## Prueba local con Docker Compose

Requiere Docker Compose. El stack usa un core falso para probar la cola sin GPU:

```powershell
docker compose up --build
```

El MCP queda en `http://127.0.0.1:8000/mcp`. PostgreSQL y RabbitMQ no exponen
puertos al host. En otra terminal, con el entorno Python instalado:

```powershell
uv venv --python 3.12
uv pip install -e ".[dev]"
.venv\Scripts\python.exe scripts\smoke.py
```

La prueba crea `audio/smoke.wav`, envía un trabajo, espera a que el worker lo
procese y comprueba el resultado. El contenido transcrito es ficticio porque
Compose usa `mock_core`. Para usar Speaches real, sustituye `WHISPER_CORE_URL`
en `mcp` y `worker`, configura su clave con `WHISPER_CORE_API_KEY` y elimina el
servicio `mock-core` de tu despliegue. Ambos procesos deben poder alcanzar el
core. El worker y el MCP deben compartir el mismo `WHISPER_AUDIO_DIR` si usas
`audio_path`.

`schema.sql` se aplica automáticamente solo cuando el volumen de PostgreSQL es
nuevo. Para una base existente, aplica el archivo como migración antes de
arrancar esta versión. No borres el volumen para actualizar el esquema.

## Ejecutar sin Compose

Instala Python 3.11+, PostgreSQL y RabbitMQ, aplica `schema.sql` y configura:

| Variable | Uso |
|---|---|
| `DATABASE_URL` | Conexión PostgreSQL para MCP y worker |
| `RABBITMQ_URL` | Conexión RabbitMQ para el worker |
| `WHISPER_CORE_URL` | URL del mock o Speaches |
| `WHISPER_CORE_API_KEY` | Clave que el MCP/worker envían al core |
| `WHISPER_SYNC_TIMEOUT_SECONDS` | Timeout de `transcribe`; default 600 |
| `WHISPER_AUDIO_DIR` | Volumen de lectura de `audio_path` |
| `WHISPER_JOB_TIMEOUT_SECONDS` | Timeout de cada transcripción larga; default 1200 |
| `WHISPER_JOB_QUEUE` | Nombre de cola; default `whisper.transcriptions` |

Después ejecuta `python -m whisper_mcp.server` y
`python -m whisper_mcp.worker` en procesos separados. En Windows se usa el
bucle de eventos Selector necesario para Psycopg.

## Semántica de los trabajos

- `submit_transcription` guarda `queued` y la fila outbox en una misma
  transacción; devuelve un UUID.
- El publicador envía el UUID a una cola durable de RabbitMQ y borra la fila
  outbox solo tras confirmación. Una caída entre ambas acciones puede repetir
  un mensaje; el worker reclama el trabajo con una actualización condicional.
- El worker confirma el mensaje una vez reclamado el trabajo. Si cae durante
  la transcripción, el lease vence y el publicador vuelve a encolarlo.
- Hay hasta tres intentos para errores transitorios. Los errores definitivos
  quedan como `failed`. `get_transcription` lee PostgreSQL.
- `cancel_transcription` marca el trabajo `cancelled`. Si Speaches ya recibió
  el audio, la llamada en curso puede continuar, pero su resultado se descarta.
- `webhook_url` se rechaza explícitamente: aún no hay entrega firmada.

## Estado y límites actuales

El límite anunciado para `transcribe` es 5 minutos, pero
`audio.estimate_duration()` todavía devuelve 10 segundos fijos. Falta medir la
duración real. La autenticación de clientes MCP y la comprobación de propiedad
de cada trabajo siguen pendientes: **no expongas este stack a Internet**. El
Compose de ejemplo solo publica el MCP en `127.0.0.1` y usa credenciales de
desarrollo. Antes de producción también hay que restringir los destinos de
`audio_url`, limitar la lectura del audio durante la descarga y medir la
capacidad del core con audios reales.

## Pruebas

```powershell
.venv\Scripts\python.exe -m pytest -q
```

La suite usa un cliente MCP en memoria y mocks para las dependencias. La
prueba `scripts/smoke.py` verifica el flujo con servicios reales de PostgreSQL
y RabbitMQ cuando Compose está disponible.
