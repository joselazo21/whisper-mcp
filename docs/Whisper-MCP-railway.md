# Desplegar Whisper MCP en Railway (CPU)

Actualizada: 14 de octubre de 2026. Idioma: español.

Railway **no ejecuta `docker-compose.yml` directamente**: cada servicio del
Compose se convierte en un servicio independiente de Railway dentro de un mismo
proyecto. Este documento mapea el `compose.yaml` local a Railway y lista las
variables y advertencias relevantes.

> Railway es **solo Linux y no ofrece GPU**. Por eso este despliegue usa la
> imagen **CPU** de Speaches con cuantización INT8 y el modelo
> `Systran/faster-whisper-small` (aprox. 4× tiempo real en CPU). Para producción
> con más volumen, valora un host con GPU (o Speaches en CUDA) fuera de Railway.

## 1. Mapeo de servicios

| Servicio del Compose | Servicio en Railway | Cómo se crea |
|---|---|---|
| `postgres` | Postgres (gestionado) | **+ New → Database → PostgreSQL** (recomendado) |
| `rabbitmq` | RabbitMQ | **+ New → Docker Image → `rabbitmq:4-management`** |
| `speaches` | Speaches | **+ New → Docker Image → `ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu`** |
| `mcp` | MCP server | **+ New → GitHub Repo** (usa el `Dockerfile`) |
| `worker` | Worker | **+ New → GitHub Repo** (mismo repo; se cambia el comando) |

### Postgres gestionado

Railway crea la base de datos y expone `DATABASE_URL` automáticamente. Sustituye
al servicio `postgres` del Compose. Aplica `schema.sql` una vez (Railway no lo
ejecuta solo): conéctate a la base de datos y ejecuta las sentencias de
`schema.sql`.

### RabbitMQ

Despliega la imagen `rabbitmq:4-management` y define las variables:

```
RABBITMQ_DEFAULT_USER=whisper
RABBITMQ_DEFAULT_PASS=<secreto>
```

El worker usará la URL `amqp://whisper:<secreto>@<rabbitmq>.railway.internal/`.

### Speaches (core de transcripción)

Imagen: `ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu`. Añade un **Volumen**
montado en `/home/ubuntu/.cache/huggingface/hub` para conservar el modelo
descargado entre despliegues.

Variables de entorno:

```
WHISPER__INFERENCE_DEVICE=cpu
WHISPER__COMPUTE_TYPE=int8
API_KEY=<secreto-largo>
ENABLE_UI=false
LOG_LEVEL=info
STT_MODEL_TTL=-1
PRELOAD_MODELS=["Systran/faster-whisper-small"]
```

- Speaches escucha en el puerto **8000** por defecto; configura ese puerto en el
  servicio de Railway.
- La primera vez descarga `Systran/faster-whisper-small` (~460 MB) al arrancar.
- `API_KEY` obliga a enviar `Authorization: Bearer <clave>` en `/v1/*`;
  `/health` queda abierto (útil como healthcheck).

## 2. MCP server y worker (desde GitHub)

Ambos usan el mismo repositorio y el mismo `Dockerfile`. La diferencia está en
el comando de arranque:

| Servicio | Start Command | Tipo |
|---|---|---|
| `mcp` | `python -m whisper_mcp.server` | Web (expone `/mcp`) |
| `worker` | `python -m whisper_mcp.worker` | Worker (sin puerto, consume de RabbitMQ) |

El `Dockerfile` por defecto lanza el server; para el worker, sobrescribe el
comando en la configuración del servicio.

### Variables de entorno (mcp y worker)

```
# mcp
DATABASE_URL=${{Postgres.DATABASE_URL}}
WHISPER_CORE_URL=http://<speaches>.railway.internal:8000
WHISPER_CORE_API_KEY=<el mismo API_KEY de Speaches>
WHISPER_MODEL_ID=Systran/faster-whisper-small
WHISPER_AUDIO_DIR=/audio
MCP_HOST=0.0.0.0
MCP_ALLOWED_HOSTS=<tu-dominio>.up.railway.app
MCP_ALLOWED_ORIGINS=https://<tu-dominio>.up.railway.app

# worker
DATABASE_URL=${{Postgres.DATABASE_URL}}
RABBITMQ_URL=amqp://whisper:<secreto>@<rabbitmq>.railway.internal/
WHISPER_CORE_URL=http://<speaches>.railway.internal:8000
WHISPER_CORE_API_KEY=<el mismo API_KEY de Speaches>
WHISPER_MODEL_ID=Systran/faster-whisper-small
WHISPER_AUDIO_DIR=/audio
```

Notas:

- `DATABASE_URL` como variable de referencia (`${{Postgres.DATABASE_URL}}`) se
  mantiene sincronizada con el Postgres gestionado.
- `WHISPER_CORE_URL` apunta al Speaches por la red privada de Railway
  (`<nombre-servicio>.railway.internal`), en el puerto 8000 que escucha Speaches.
- El puerto del MCP lo fija `PORT` (Railway lo inyecta); el código ya lo lee
  (`MCP_PORT` → `PORT` → `8000`).
- `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` son necesarios porque al exponer el
  MCP en `0.0.0.0` la protección DNS-rebinding exige declarar los orígenes
  permitidos. Pon el dominio público de Railway.

## 3. Networking y dominio

- Los servicios se comunican por la red privada de Railway con
  `<nombre-servicio>.railway.internal`. No hay que publicar Speaches, RabbitMQ ni
  Postgres a Internet.
- Solo el **MCP** necesita dominio público: **Settings → Networking → Generate
  Domain**. El endpoint queda en `https://<dominio>/mcp`.

## 4. RAM y coste (importante en CPU)

Railway cobra por RAM-segundo. Speaches con `small` en INT8 funciona con
~2–4 GB de RAM, pero el modelo cargado mantiene la RAM ocupada de forma
continua. Presupuesto orientativo:

- **MCP + worker**: 0.5–1 GB cada uno (procesos Python ligeros).
- **Speaches (small, INT8)**: 2–4 GB, siempre ocupado mientras corra.
- **Postgres / RabbitMQ**: los mínimos de Railway.

El plan Free de Railway ($1/mes de crédito) **no alcanza** para este stack. El
piloto realista es el plan Hobby ($5/mes mínimo) o el trial de $5. Mide el
consumo real y apaga Speaches cuando no lo uses para no acumular RAM-segundo.

## 5. Probar de extremo a extremo

Con los servicios arriba:

```bash
curl https://<dominio>/healthz
# -> {"status":"ok"}

curl https://<dominio>/readyz
# -> {"status":"ready"} (si Speaches y Postgres responden)
```

Una transcripción por el MCP (a través de un cliente MCP) o directamente contra
Speaches (solo en local / red interna):

```bash
curl http://<speaches>.railway.internal:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer <API_KEY>" \
  -F "model=Systran/faster-whisper-small" \
  -F "file=@prueba-es.wav" \
  -F "language=es"
```

## 6. Siguientes pasos

- Fijar el **digest** de la imagen de Speaches en lugar de la etiqueta
  `0.9.0-rc.3-cpu` para producción reproducible.
- Añadir autenticación por cliente en el MCP (hoy el MCP está abierto; la clave
  actual solo autentica MCP→Speaches, no al cliente MCP). Ver
  `docs/Whisper-MCP-diseno.md` §9.
- Si el volumen crece o necesitas más velocidad, pasar a Speaches CUDA en un
  host con GPU (fuera de Railway).
