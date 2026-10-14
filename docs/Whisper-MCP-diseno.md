# Diseño del servidor MCP de Whisper

Actualizada: 12 de octubre de 2026. Idioma: español.

Objetivo: exponer el servicio de transcripción de voz (Whisper) como un **servidor MCP** reutilizable, para que cualquier componente del entorno (asistentes, agentes, otros servicios) lo invoque sin depender de él. Este documento define el **contrato MCP**: transporte, tools, autenticación, manejo de audio y flujos. El motor de transcripción (core) está cubierto en `Whisper-guia-hosting-servidor.md`.

**Estado:** contrato implementado, validación de duración real y flujo de trabajos con PostgreSQL, RabbitMQ y worker. El despliegue real con Speaches y la autenticación multiempresa siguen pendientes.

---

## 1. Principio rector

> El servidor MCP no sabe qué existe fuera. Solo expone: *recibe audio, devuelve transcripción*. Todo lo demás (quién llama, para qué, con qué frecuencia) es configuración, no diseño.

Esto obliga a desacoplar dos capas:

1. **La interfaz MCP (nuestra)** — el contrato estable que los consumidores usan. No cambia aunque cambie el motor.
2. **El motor (faster-whisper)** — detalle interno reemplazable.

Los consumidores hablan con **nuestro MCP**, jamás con el motor (faster-whisper) directamente. Cambiar de motor no debe tocar una sola línea del cliente.

## 2. Qué es (y qué no es) el MCP server

Un adaptador **delgado y sin estado local** que traduce tool-calls MCP a llamadas al core y operaciones de trabajos en PostgreSQL.

**Hace:**
- Recibir requests MCP (JSON-RPC). La autenticación de clientes está pendiente.
- Validar y normalizar el input de audio.
- Enviar `transcribe` al core y gestionar los trabajos largos en PostgreSQL.
- Traducir respuestas del core a `content` MCP, y errores a errores estructurados.
- Exponer metadatos (modelo, idiomas, límites) como recurso.

**No hace:** inferencia, segmentación, VAD, diarización ni cuotas. La cola y la persistencia de trabajos son componentes nuestros separados del core.

## 3. Arquitectura

```text
[ agentes / componentes con cliente MCP ]  (OpenCode, Claude, tu app, otros servicios)
        │ MCP (JSON-RPC, Streamable HTTP)
        ▼
MCP Server (sin estado local, sin GPU)
   ├── transcribe ──REST──► Speaches/faster-whisper
   └── trabajos ──────────► PostgreSQL (estado + outbox)
                                  │
                                  ▼
                              RabbitMQ ──► worker ──REST──► Speaches
```

```text
Nginx :443 (TLS)
   ├─ /mcp/*        → whisper-mcp (N réplicas, stateless)
   └─ (el core queda en red privada, no expuesto)
```

## 4. Transporte y endpoints

**Transporte: Streamable HTTP** (estándar actual; SSE deprecado). Permite un proceso único sirviendo a muchos clientes, auth por header y ruteo por intermediarios.

| Método | Ruta | Uso |
|---|---|---|
| `POST` | `/mcp` | Entrada JSON-RPC (`initialize`, `tools/list`, `tools/call`, notificaciones). |
| `GET` | `/mcp` | Upgrade a SSE opcional para notificaciones server→client (progreso). |
| `GET` | `/healthz` | Liveness. |
| `GET` | `/readyz` | Readiness (comprueba conectividad al core). |
| `GET` | `/metrics` | Propuesto; aún no implementado. |

Handshake: `initialize` declara `capabilities.tools` y `protocolVersion`. El cliente pide `tools/list` una vez y cachea el schema.

## 5. Contrato: las tools

### 5.1 `transcribe` (síncrono, clips ≤5 minutos)

```json
{
  "name": "transcribe",
  "description": "Transcribe un clip de audio de hasta 5 minutos a texto. Para audio más largo usa submit_transcription. Proporciona exactamente UNA de audio_url, audio_base64 o audio_path.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "audio_url": {
        "type": "string",
        "format": "uri",
        "description": "URL accesible por el servidor (S3/objeto interno). Recomendado para producción."
      },
      "audio_base64": {
        "type": "string",
        "description": "Contenido del audio en base64. Solo clips pequeños (hasta 5 MB)."
      },
      "audio_path": {
        "type": "string",
        "description": "Ruta en un volumen compartido con el servidor."
      },
      "language": {
        "type": "string",
        "description": "Código ISO-639-1, p. ej. 'es'. Opcional."
      },
      "prompt": {
        "type": "string",
        "description": "Contexto/ortografía a sesgar (nombres, cifras, jerga del cliente)."
      },
      "response_format": {
        "type": "string",
        "enum": ["json", "text", "verbose_json"],
        "default": "json"
      }
    }
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "text": { "type": "string" },
      "language": { "type": "string" },
      "segments": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "start": { "type": "number" },
            "end": { "type": "number" },
            "text": { "type": "string" }
          }
        }
      }
    }
  }
}
```

### 5.2 `submit_transcription` (asíncrono, audios largos)

```json
{
  "name": "submit_transcription",
  "description": "Encola una transcripción de audio largo (llamadas/reuniones). Devuelve un job_id para consultar estado con get_transcription.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "audio_url": { "type": "string", "format": "uri" },
      "audio_path": { "type": "string" },
      "language": { "type": "string" },
      "webhook_url": {
        "type": "string",
        "format": "uri",
        "description": "Reservado para una futura entrega por webhook; no disponible aún."
      }
    }
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "job_id": { "type": "string" },
      "status": { "type": "string", "enum": ["queued"] },
      "accepted": { "type": "boolean", "const": true }
    }
  }
}
```

### 5.3 `get_transcription` (polling)

```json
{
  "name": "get_transcription",
  "description": "Consulta el estado de un trabajo de transcripción.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "job_id": { "type": "string" }
    },
    "required": ["job_id"]
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "status": {
        "type": "string",
        "enum": ["queued", "processing", "done", "failed", "cancelled"]
      },
      "text": { "type": "string" },
      "segments": { "type": "array", "items": { "type": "object" } },
      "progress": { "type": "number", "minimum": 0, "maximum": 1 },
      "error": { "type": "string" }
    },
    "required": ["status"]
  }
}
```

### 5.4 `cancel_transcription`

```json
{
  "name": "cancel_transcription",
  "description": "Cancela un trabajo en cola o en proceso. Idempotente.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "job_id": { "type": "string" }
    },
    "required": ["job_id"]
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "cancelled": { "type": "boolean" }
    }
  }
}
```

### 5.5 Recurso (metadatos, no tool)

`whisper://info` → `{ "model": "Systran/faster-whisper-large-v3", "languages": ["es", "en", ...], "engine": "faster-whisper", "max_sync_seconds": 300 }`.

Permite al cliente conocer límites sin hardcodearlos (p. ej. cuántos segundos son "cortos").

### 5.6 Notas de diseño del contrato

- `required: []` a propósito: la validación de "exactamente una fuente de audio" se hace en el handler y devuelve un error claro. JSON Schema `oneOf` con objetos anidados confunde a los LLM.
- Las descripciones guían al LLM: cada tool dice qué hace, cuándo usarla y qué alternativas existen.

## 6. Manejo de audio

| Fuente | Cuándo | Límite a imponer |
|---|---|---|
| `audio_url` | Audio ya en objeto/almacenamiento interno | Descarga con timeout + límite de tamaño; solo `http/https` (evitar SSRF) |
| `audio_path` | Cliente y server comparten volumen | Ruta dentro de un directorio permitido (sin `../`) |
| `audio_base64` | Clips pequeños y aislados | Rechazar >5 MB antes de decodificar |

Regla de validación (aplica a las 3): decodificar lo mínimo para verificar **duración real**. Si supera los 5 minutos y la tool fue `transcribe`, responder con error apuntando a `submit_transcription`. **No confiar en el tamaño del archivo para estimar duración** (un MP3 chico puede ser una hora de audio).

## 7. Cómo envía audio un LLM

> El LLM no "envía" audio: solo escribe texto (los argumentos de la tool). Los bytes viajan por otro camino.

```text
[ bytes del audio ]  →  almacenamiento (S3 / volumen / memoria del host)
        │  referencia (url / path / base64)
        ▼
[ LLM ]  →  escribe tool call con la referencia  →  [ cliente MCP ]  →  [ MCP server ]  →  [ core Whisper ]
        ◄────────────────────────── texto ──────────────────────────────────────────────────────┘
```

- **Por referencia (normal):** el audio ya está en S3/volumen. El LLM copia la URL/ruta en los argumentos. El MCP server descarga/lee los bytes. El LLM nunca ve los bytes.
- **Por base64 (clips pequeños):** lo inyecta el cliente/host, no el LLM. Solo si el host ya tiene el audio en memoria y es chico.
- **Por ruta compartida:** si cliente y servidor comparten filesystem.

Recomendación: `audio_url` como camino principal, `audio_path` para Compose local, `audio_base64` solo como conveniencia.

## 8. Flujos internos

### 8.1 Síncrono (`transcribe`)

```text
cliente ──tools/call {transcribe}──► MCP server
  1. valida auth (Bearer api_key; pendiente)
  2. valida fuente de audio + duración (≤5 minutos)
  3. POST core /v1/audio/transcriptions (multipart, timeout configurable; default 600s)
  4. 200  → content [ {type:"text", text} ]
  5. timeout → content [ {type:"text", text:"usa submit_transcription"} ] + isError
```

### 8.2 Asíncrono (`submit_transcription` → `get_transcription`)

```text
cliente ──submit_transcription──► MCP ──INSERT job + outbox──► PostgreSQL
cliente ◄── {job_id} ────────────────────────────────────────────────┘
publicador outbox ──► RabbitMQ ──► worker ──POST /v1/audio/transcriptions──► Speaches
worker ──guarda resultado──► PostgreSQL
cliente ──get_transcription──► MCP ──SELECT job──► PostgreSQL → {status, text?}
```

`webhook_url` todavía se rechaza explícitamente. La entrega firmada por HMAC es una propuesta futura.

## 9. Autenticación y multi-tenant

- **Pendiente:** el MCP debe autenticar cada request y asociar `job_id` a su propietario en PostgreSQL.
- La clave actual `WHISPER_CORE_API_KEY` solo autentica el MCP y el worker ante Speaches; no identifica al cliente MCP.
- No debe exponerse públicamente ni prometer aislamiento multiempresa hasta implementar la comprobación de propiedad en `get_transcription` y `cancel_transcription`.
- Streamable HTTP permite que Nginx inspeccione/rote por header sin parsear el body JSON-RPC.

### 9.1 Cómo llega la clave al usuario

La clave **no la entrega el MCP en su "instalación"**. Se emite fuera de banda y se configura en el cliente:

```text
1. PROVISIONAR (tú): emites una api_key por cliente.
2. ENTREGAR (tu canal): dashboard, correo, secret manager...
3. CONFIGURAR (el usuario): la pega en la config del servidor de su cliente.
4. USAR (automático): cada request lleva Authorization: Bearer <clave>.
```

Ejemplo en Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "whisper": {
      "url": "https://whisper.empresa.example/mcp",
      "headers": {
        "Authorization": "Bearer sk-whisper-a1b2c3..."
      }
    }
  }
}
```

MCP no define la emisión de claves en Speaches. El flujo anterior sigue siendo un diseño, no una función implementada.

### 9.2 División de responsabilidades

| Qué | ¿De quién es? |
|---|---|
| Servidor MCP (tools, lógica, auth) | **Nuestro** |
| Motor Whisper (core) | **Nuestro** |
| Cliente/host MCP (Claude, OpenCode, tu app) | **Del consumidor** |
| Código cliente (si tu backend llama por SDK) | **Nuestro, como consumidor aparte** |

## 10. Errores

- **Error de negocio** (audio largo, formato inválido, job no encontrado): `tools/call` con `isError: true` y `content: [{type:"text", text:"<mensaje claro>"}]`. El LLM lo lee y corrige.
- **Error de protocolo** (método desconocido, JSON malformado): código JSON-RPC (`-32601` method not found, `-32700` parse error, `-32602` invalid params).
- **Error de auth**: código propio (p. ej. `-32001`) + mensaje "api_key inválida o ausente".

Mapeo core→MCP:

| Core devuelve | MCP responde |
|---|---|
| `400` validación | `isError` + mensaje accionable |
| `401/403` | error de auth |
| `404` recurso del core no existe | `isError` + "recurso no encontrado" |
| `429` cuota | `isError` + "cuota excedida, reintenta" |
| `5xx` | `-32603` internal + retry-safe |

## 11. Empaquetado y despliegue (portable)

- **Procesos:** servidor MCP y worker Python separados; el worker también publica el outbox.
- **Imagen:** misma base que el core, **sin GPU** (el MCP server no necesita GPU).
- **Health:** `/readyz` comprueba conectividad al core; `/healthz` solo liveness.
- **Compose local:** `mcp`, `worker`, `postgres`, `rabbitmq` y `speaches` (core); el ejemplo solo publica MCP en localhost. El `mock_core` sigue disponible como utilidad de pruebas.
- **K8s:** `Deployment` (stateless, N réplicas) + `Service` + `Ingress` (Nginx/TLS) apuntando al core por DNS interno. Mismo código, sin cambios.

## 12. Comunidad: por qué construir el nuestro

Existen MCPs de Whisper comunitarios, pero **ninguno validado para servidor multi-cliente**:

- [arcaputo3/mcp-server-whisper](https://github.com/arcaputo3/mcp-server-whisper) — usa la API cloud de OpenAI (no self-hosted); stdio.
- [jwulff/whisper-mcp](https://github.com/jwulff/whisper-mcp) — whisper.cpp local, limpio, pero stdio y un solo usuario.
- [BigUncle/Fast-Whisper-MCP-Server](https://github.com/BigUncle/Fast-Whisper-MCP-Server) — faster-whisper, alto rendimiento, stdio.

Todos: stdio (no Streamable HTTP), sin multi-tenant, sin colas ni audios largos. Sirven como referencia de naming/motor, no como base.

## 13. Checklist de decisión antes de construir

- [ ] Transporte Streamable HTTP confirmado (no stdio).
- [ ] Las 4 tools + recurso `whisper://info` congelados como contrato.
- [x] Manejo de audio: url/path/base64 + validación de duración real.
- [ ] Auth: emisión/revocación de claves por cliente + verificación de pertenencia de `job_id`.
- [ ] Errores: mapeo core→MCP y códigos JSON-RPC definidos.
- [ ] `whisper-mcp` como Deployment stateless; core en red privada.

## 14. Fuentes principales

1. [Model Context Protocol — transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)
2. [Streamable HTTP transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
3. [SDK oficial de MCP (Python)](https://github.com/modelcontextprotocol/python-sdk)
4. [File Uploads Working Group (MCP)](https://modelcontextprotocol.io/community/working-groups/file-uploads)
5. `Whisper-guia-hosting-servidor.md` (core del servicio)
