# Whisper MCP

Servidor MCP para transcribir audio. Los clips de hasta 5 minutos se envían
directamente al core de transcripción. Para audios largos, el servidor guarda el
trabajo en PostgreSQL y un worker lo procesa con ayuda de RabbitMQ. Docker
Compose usa `mock_core` para las pruebas locales; el despliegue puede usar
Speaches.

## Probarlo en local

Necesitas Docker Compose, `uv` y Python 3.12. Desde la raíz del proyecto,
inicia los servicios:

```powershell
docker compose up --build
```

El servidor MCP queda en `http://127.0.0.1:8000/mcp`. PostgreSQL y RabbitMQ
solo son accesibles dentro de Compose. En otra terminal, instala el proyecto y
ejecuta la prueba de extremo a extremo:

```powershell
uv venv --python 3.12
uv pip install -e ".[dev]"
.venv\Scripts\python.exe scripts\smoke.py
```

La prueba crea `audio/smoke.wav`, transcribe un clip, envía un trabajo a la cola
y consulta su resultado. La transcripción es ficticia porque Compose usa
`mock_core`. También puedes comprobar la disponibilidad del servidor en
`http://127.0.0.1:8000/healthz` y la conexión con el core y PostgreSQL en
`http://127.0.0.1:8000/readyz`.

## Herramientas MCP

| Herramienta | Uso |
|---|---|
| `transcribe` | Transcribe un clip de hasta 5 minutos y devuelve `text`, `language` y `segments`. |
| `submit_transcription` | Encola un audio largo y devuelve un `job_id`. |
| `get_transcription` | Consulta el estado y, cuando esté disponible, el resultado del trabajo. |
| `cancel_transcription` | Cancela un trabajo en cola o en proceso. |

`transcribe` requiere exactamente una fuente: `audio_url`, `audio_base64` o
`audio_path`. El audio en base64 admite hasta 5 MB; las URL y rutas admiten
hasta 25 MB. Para `submit_transcription`, proporciona `audio_url` o `audio_path`;
el worker admite hasta 200 MB. Una ruta de `audio_path` se resuelve dentro de
`WHISPER_AUDIO_DIR`, que debe estar disponible tanto para el MCP como para el
worker. El recurso `whisper://info` expone el modelo, los idiomas anunciados y
el límite de duración de `transcribe`.

El flujo de un trabajo largo es:

```text
submit_transcription -> job_id -> get_transcription -> done / failed / cancelled
```

El estado también puede ser `queued` o `processing`. `webhook_url` está
reservado y se rechaza por ahora; consulta el resultado con
`get_transcription`. En los formatos `json` y `text`, el core puede omitir el
idioma y los segmentos. En ese caso, `transcribe` devuelve `language: null` y
`segments: []`.

## Configurar el core y los servicios

Para ejecutar el proyecto sin Compose, necesitas Python 3.11 o posterior,
PostgreSQL y RabbitMQ. Instala el paquete en un entorno virtual (por ejemplo,
con `uv pip install -e .`), aplica `schema.sql` a PostgreSQL y configura las
variables de entorno. Después, inicia ambos procesos:

```powershell
python -m whisper_mcp.server
python -m whisper_mcp.worker
```

Inicia cada proceso en una terminal distinta. En Windows, ambos configuran el
bucle de eventos Selector que necesita Psycopg.

| Variable | Uso |
|---|---|
| `DATABASE_URL` | Conexión PostgreSQL del MCP y del worker. |
| `RABBITMQ_URL` | Conexión RabbitMQ del worker. |
| `WHISPER_CORE_URL` | URL base del mock o de Speaches. |
| `WHISPER_CORE_API_KEY` | Clave enviada al core como token Bearer, si se configura. |
| `WHISPER_MODEL_ID` | Modelo enviado al core; por defecto, `Systran/faster-whisper-large-v3`. |
| `WHISPER_AUDIO_DIR` | Directorio permitido para `audio_path`. |
| `WHISPER_SYNC_TIMEOUT_SECONDS` | Tiempo máximo de la llamada síncrona al core; por defecto, 600 segundos. |
| `WHISPER_JOB_TIMEOUT_SECONDS` | Tiempo máximo de cada llamada del worker al core; por defecto, 1200 segundos. |
| `WHISPER_JOB_QUEUE` | Nombre de la cola; por defecto, `whisper.transcriptions`. |
| `MCP_HOST`, `MCP_PORT` | Dirección y puerto del servidor; por defecto, `127.0.0.1:8000`. |
| `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS` | Hosts y orígenes permitidos para la protección contra DNS rebinding cuando se configuran. |

Para usar Speaches, cambia `WHISPER_CORE_URL` en el MCP y en el worker y, si
corresponde, configura `WHISPER_CORE_API_KEY` en ambos. Deben poder alcanzar el
endpoint `/v1/audio/transcriptions` del core. RabbitMQ solo transporta el
`job_id`; el audio permanece en un volumen compartido o en una URL accesible
para el worker.

En Compose, `schema.sql` se aplica automáticamente solo al crear un volumen de
PostgreSQL. Si ya existe una base de datos, aplica el esquema como migración
antes de iniciar esta versión. No borres el volumen para actualizarlo.

## Cómo se procesan los trabajos

`submit_transcription` guarda el trabajo y una fila outbox en la misma
transacción. El publicador envía el `job_id` a una cola durable y elimina la
fila outbox tras la confirmación de RabbitMQ. Si ocurre una caída entre esas
acciones, puede llegar un mensaje duplicado; el worker reclama el trabajo con
una actualización condicional en PostgreSQL.

El worker confirma el mensaje tras reclamar el trabajo. Si deja de responder
durante la transcripción, vence su lease y el publicador lo vuelve a encolar.
Los errores transitorios admiten hasta tres intentos; los errores definitivos
dejan el trabajo en `failed`. Si se cancela un trabajo que ya llegó al core, la
llamada puede continuar, pero se descarta su resultado.

## Límites y seguridad

`transcribe` usa FFmpeg para medir la duración real del audio y rechaza
archivos inválidos o clips de más de 5 minutos. La validación tiene un límite
de 60 segundos. El servidor todavía no autentica a los clientes MCP ni
comprueba la propiedad de cada trabajo: **no expongas este stack a Internet**.
Compose solo publica el MCP en `127.0.0.1` y usa credenciales de desarrollo.

Antes de un despliegue de producción, restringe los destinos de `audio_url` y
mide la capacidad del core con audios reales. Consulta el
[diseño](docs/Whisper-MCP-diseno.md) y las
[buenas prácticas](docs/Whisper-MCP-buenas-practicas.md) para el contrato y las
decisiones del proyecto.

## Pruebas

```powershell
.venv\Scripts\python.exe -m pytest -q
```

La suite usa un cliente MCP en memoria y mocks de las dependencias.
`scripts/smoke.py` comprueba el flujo con PostgreSQL y RabbitMQ cuando Compose
está disponible.
