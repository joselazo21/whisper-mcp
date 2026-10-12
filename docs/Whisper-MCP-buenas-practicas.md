# Whisper MCP — Buenas prácticas que cumple

Actualizada: 12 de octubre de 2026. Idioma: español.

Documento de referencia que explica, una a una, las buenas prácticas del
**Model Context Protocol (MCP)** que cumple el servidor `whisper-mcp`, con la
fuente normativa de cada una y cómo está implementada en el código.

- **Spec contrastada:** MCP `2026-07-28` (tools, resources, Streamable HTTP).
- **SDK:** `mcp` 2.3.0 (Python).
- **Código:** `whisper-mcp/src/whisper_mcp/server.py`, `audio.py`, `core_client.py`.

> El contrato funcional de las tools y del recurso está en
> `Whisper-MCP-diseno.md`. Este documento solo cubre **cumplimiento de buenas
> prácticas del protocolo**, no la lógica de negocio.

---

## Resumen

| # | Buena práctica | Estado |
|---|---|---|
| 1 | Transporte Streamable HTTP (estándar actual) | ✅ |
| 2 | Endpoint único con POST | ✅ |
| 3 | Bind localhost por defecto + protección DNS-rebinding | ✅ |
| 4 | Identidad del servidor (`name` + `version` semver) | ✅ |
| 5 | `instructions` para guiar al cliente/LLM | ✅ |
| 6 | Nombres de tools válidos y únicos | ✅ |
| 7 | Orden determinista de tools | ✅ |
| 8 | `inputSchema` válido y plano | ✅ |
| 9 | Descripciones accionables | ✅ |
| 10 | Tool annotations (`readOnlyHint`, `idempotentHint`, `destructiveHint`) | ✅ |
| 11 | Estado vía handle explícito, sin estado por conexión | ✅ |
| 12 | URI de recurso RFC 3986 + `mimeType` correcto | ✅ |
| 13 | Errores de negocio como `isError: true` (no errores de protocolo) | ✅ |
| 14 | Mensajes de error accionables | ✅ |
| 15 | Mapeo core→MCP de códigos HTTP | ✅ |
| 16 | Validación de entrada + anti path-traversal + anti-SSRF | Parcial: falta bloquear destinos internos directos |
| 17 | Desacoplamiento del motor (motor intercambiable) | ✅ |
| 18 | Sin GPU / sin estado (adaptador delgado) | ✅ |

---

## 1. Transporte Streamable HTTP

**Buena práctica.** Usar el transporte actual del protocolo. La revisión
`2026-07-28` es la vigente; el transporte HTTP+SSE está **deprecado** desde
`2025-03-26` y Streamable HTTP es su reemplazo.

**Cómo se cumple.** El servidor se ejecuta con `transport="streamable-http"`:

```python
mcp.run(
    transport="streamable-http",
    host=host,
    port=int(os.getenv("MCP_PORT", "8000")),
    streamable_http_path="/mcp",
)
```

El SDK sirve el endpoint `/mcp` que acepta POST y, por petición, responde con
`application/json` o un stream SSE.

**Fuente:** [Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)

---

## 2. Endpoint único con POST

**Buena práctica.** El servidor **MUST** exponer un único endpoint (el "MCP
endpoint") que acepte POST; cada mensaje JSON-RPC viaja en su propio POST.

**Cómo se cumple.** Se publica una sola ruta (`/mcp`) vía `streamable_http_path`.
Las rutas extra (`/healthz`, `/readyz`) son de infraestructura, no del protocolo,
y se registran con `@mcp.custom_route(...)` (la API del SDK pensada para health
checks y similares).

**Fuente:** [Streamable HTTP — Sending Messages](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)

---

## 3. Bind localhost por defecto + protección DNS-rebinding

**Buena práctica.** La spec exige que el servidor valide la cabecera `Origin`
(contra ataques DNS-rebinding) y recomienda, en local, enlazar a `127.0.0.1` en
lugar de `0.0.0.0`.

**Cómo se cumple.** El host por defecto es localhost:

```python
host = os.getenv("MCP_HOST", "127.0.0.1")
```

El SDK activa la validación de `Origin`/`Host` automáticamente cuando el host es
localhost. Para exponer el servidor (contenedor/remoto), se exige configurar
explícitamente los orígenes/hosts permitidos:

```python
def _transport_security(host):
    allowed_hosts   = [h for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h]
    allowed_origins = [o for o in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if o]
    if allowed_hosts or allowed_origins:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )
    return None
```

**Fuente:** [Streamable HTTP — Security & Endpoint](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)

---

## 4. Identidad del servidor (`name` + `version` semver)

**Buena práctica.** Todo servidor MCP se identifica con `serverInfo`, que debe
incluir `name` y `version` (semver). Es lo que los clientes muestran y usan para
trazar de dónde vino un resultado.

**Cómo se cumple.**

```python
mcp = MCPServer(
    name="whisper-mcp",
    title="Whisper (transcripción de voz)",
    description="Transcripción de audio a texto vía faster-whisper, expuesta como API MCP.",
    version="0.1.0",
    ...
)
```

- `name` es un identificador estable, sin espacios.
- `version` sigue semver (`0.1.0`).

---

## 5. `instructions` para guiar al cliente/LLM

**Buena práctica.** `instructions` describe cómo debe usar el servidor el
cliente/LLM. Mejora el comportamiento de los agentes que consumen las tools.

**Cómo se cumple.**

```python
instructions=(
    "Usa 'transcribe' para clips de hasta 5 minutos y "
    "'submit_transcription' + 'get_transcription' para audios largos. "
    "Proporciona exactamente UNA fuente de audio."
)
```

---

## 6. Nombres de tools válidos y únicos

**Buena práctica.** Los nombres deben tener 1–128 caracteres y usar solo letras
ASCII, dígitos, `_`, `-`, `.`; sin espacios; únicos dentro del servidor.

**Cómo se cumple.** Los cuatro nombres usan `snake_case` y son únicos:

```
transcribe
submit_transcription
get_transcription
cancel_transcription
```

**Fuente:** [Tools — Tool Names](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)

---

## 7. Orden determinista de tools

**Buena práctica.** El servidor **SHOULD** devolver las tools en orden
determinista (mismo orden ante peticiones repetidas) para que los clientes
puedan cachear la lista y mejorar el cache de prompt del LLM.

**Cómo se cumple.** Las tools se registran en orden fijo en el módulo y el SDK
las devuelve en ese orden de registro, sin variar por petición ni por conexión.

**Fuente:** [Tools — Capabilities](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)

---

## 8. `inputSchema` válido y plano

**Buena práctica.** `inputSchema` **MUST** ser un objeto JSON Schema válido (no
`null`). Nuestro contrato además exige campos **planos** en la raíz.

**Cómo se cumple.** Se usan parámetros de función con `Annotated[…, Field]`, de
modo que el SDK genera un esquema plano:

```python
async def transcribe(
    audio_url: Annotated[str | None, Field(description="URL accesible...")] = None,
    audio_base64: Annotated[str | None, Field(description="...")] = None,
    ...
) -> str:
```

Resultado (verificado con `tools/list`): `properties` con `audio_url`,
`audio_base64`, `audio_path`, `language`, `prompt`, `response_format` en la raíz,
sin anidamiento bajo un parámetro `inp`.

**Fuente:** [Tools — Data Types](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)

---

## 9. Descripciones accionables

**Buena práctica.** El LLM decide qué tool usar leyendo `description`. Debe
explicar qué hace, cuándo usarla y qué alternativas existen.

**Cómo se cumple.** Cada tool lleva una descripción que orienta la elección:

```python
description=(
    "Transcribe un clip de audio de hasta 5 minutos a texto. "
    "Para audio más largo usa submit_transcription. "
    "Proporciona exactamente UNA de audio_url, audio_base64 o audio_path."
)
```

---

## 10. Tool annotations

**Buena práctica.** Las annotations (`readOnlyHint`, `destructiveHint`,
`idempotentHint`, `openWorldHint`) informan al cliente del comportamiento de la
tool y habilitan confirmaciones/UI más seguras.

**Cómo se cumple.**

```python
# Solo lee estado: no muta nada.
@mcp.tool(..., annotations=ToolAnnotations(read_only_hint=True))
async def get_transcription(...): ...

# Cancela un trabajo: muta estado y es idempotente.
@mcp.tool(..., annotations=ToolAnnotations(idempotent_hint=True, destructive_hint=True))
async def cancel_transcription(...): ...
```

**Fuente:** [Tools — Tool Result / annotations](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)

---

## 11. Estado vía handle explícito (sin estado por conexión)

**Buena práctica.** MCP no tiene sesión a nivel de protocolo. El estado que
sobrevive entre llamadas debe devolverse como un **handle explícito** (un string)
en el resultado y aceptarse como argumento en las siguientes llamadas.

**Cómo se cumple.** El flujo asíncrono devuelve un `job_id` y lo recibe en las
siguientes tools:

```text
submit_transcription  →  {"job_id": "job_...", "status": "queued"}
get_transcription(job_id)  →  {"status": "done", "text": ...}
cancel_transcription(job_id)  →  {"cancelled": true}
```

El servidor no guarda estado por conexión: el `job_id` es el único vínculo.

**Fuente:** [Tools — Stateful Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)

---

## 12. URI de recurso RFC 3986 + `mimeType` correcto

**Buena práctica.** Todo recurso se identifica con un URI RFC 3986. Los esquemas
custom están permitidos siempre que cumplan RFC 3986. El `mimeType` ayuda al
cliente a interpretar el contenido.

**Cómo se cumple.**

```python
@mcp.resource(
    "whisper://info",
    name="info",
    description="Metadatos del servicio (modelo, idiomas, límites).",
    mime_type="application/json",
)
def info() -> str: ...
```

- `whisper://info` es un esquema custom válido.
- El contenido es JSON y el `mimeType` lo declara como `application/json`.

**Fuente:** [Resources — Common URI Schemes](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)

---

## 13. Errores de negocio como `isError: true` (no errores de protocolo)

**Buena práctica.** La spec distingue dos mecanismos:

1. **Errores de protocolo** (tool desconocida, petición malformada) → JSON-RPC
   error (`-32601`, `-32602`, `-32700`, …).
2. **Errores de ejecución** (validación, fallo de API, lógica de negocio) →
   resultado con `isError: true` y texto accionable, para que el LLM pueda
   autocorregirse y reintentar.

**Cómo se cumple.** Los errores de negocio se lanzan como `ToolError` y el SDK
los devuelve como `isError: true`:

```python
@_as_tool_error
async def transcribe(...) -> str:
    raw, filename = await resolve_audio_bytes(...)   # puede lanzar AudioValidationError
    ...
```

```python
def _as_tool_error(fn):
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except (AudioValidationError, CoreError) as exc:
            raise ToolError(str(exc)) from exc
    return wrapper
```

Los errores de protocolo (método desconocido, params inválidos, versión no
soportada) los maneja el SDK con los códigos JSON-RPC correctos, sin intervención
nuestra.

**Fuente:** [Tools — Error Handling](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)

---

## 14. Mensajes de error accionables

**Buena práctica.** El texto de un error de ejecución debe permitir que el LLM
entienda qué corregir.

**Cómo se cumple.** Ejemplos reales (verificados):

- Sin fuente de audio → `"Proporciona exactamente UNA de audio_url, audio_base64 o audio_path."`
- Job inexistente → `"job_id no encontrado."` (consulta en PostgreSQL).
- Cuota excedida → `"cuota excedida (429), reintenta más tarde."`

---

## 15. Mapeo core→MCP de códigos HTTP

**Buena práctica.** El MCP es un adaptador delgado: traduce la semántica del
core a errores claros para el cliente, en lugar de exponer detalles internos.

**Cómo se cumple.** `core_client.py` mapea cada status a un mensaje accionable:

```python
def _map_error(status, body):
    if status in (401, 403):
        return CoreError("api_key inválida o ausente en el core (401/403).", status)
    if status == 404:
        return CoreError("recurso no encontrado en el core (404).", status)
    if status == 429:
        return CoreError("cuota excedida (429), reintenta más tarde.", status)
    if status >= 500:
        return CoreError(f"error interno del core ({status}).", status)
    return CoreError(f"el core respondió {status}: {body[:200]}", status)
```

Esto corresponde al diseño de `Whisper-MCP-diseno.md` (§10, "Mapeo core→MCP").
Los errores de búsqueda de trabajos se producen en PostgreSQL, no en el core.

---

## 16. Validación de entrada, anti path-traversal y anti-SSRF

**Buena práctica.** La spec exige a los servidores **validar todos los inputs de
las tools**. Con `file://` y URLs hay que prevenir traversal y SSRF.

**Estado real.** En `audio.py` se exige exactamente una fuente, se resuelven
rutas reales dentro del directorio permitido, se corta la lectura al superar
el tamaño máximo y no se siguen redirecciones. Para `audio_url` solo se comprueba
el esquema `http/https`; una URL inicial puede apuntar directamente a una IP
privada. Por eso la protección SSRF todavía está incompleta.

```python
MAX_BASE64_BYTES = 5 * 1024 * 1024      # tope base64
MAX_URL_BYTES    = 25 * 1024 * 1024     # tope de descarga

# exactamente una fuente
sources = [s for s in (audio_url, audio_base64, audio_path) if s]
if len(sources) != 1:
    raise AudioValidationError("Proporciona exactamente UNA de ...")

# comprobación de esquema; no equivale a protección SSRF completa
if not audio_url.startswith(("http://", "https://")):
    raise AudioValidationError("audio_url debe ser http/https.")
```

**Fuente:** [Tools — Security Considerations](https://modelcontextprotocol.io/specification/2026-07-28/server/tools),
[Resources — Security Considerations](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)

---

## 17. Desacoplamiento del motor (motor intercambiable)

**Buena práctica.** Aunque no es un requisito del protocolo, es la buena
práctica de diseño que dicta el proyecto: el contrato MCP no debe cambiar aunque
cambie el motor de transcripción.

**Cómo se cumple.** El servidor habla con un core vía `WHISPER_CORE_URL`:

```text
transcribe ──(REST)──► core = mock (local) | Speaches/faster-whisper (producción)
trabajos largos ──► PostgreSQL + RabbitMQ ──► worker ──(REST)──► core
```

Cambiar el motor no toca ni una línea del cliente MCP. El recurso
`whisper://info` incluso anuncia el `engine` y la `core_url` actuales.

---

## 18. Sin GPU y sin estado (adaptador delgado)

**Buena práctica.** Separar la interfaz (MCP) del cómputo (core). El MCP no
debería requerir GPU ni guardar estado, para poder escalarlo como réplicas
estateless.

**Cómo se cumple.**

- El servidor MCP no importa ninguna librería de inferencia ni usa GPU.
- No mantiene estado por conexión (todo estado asíncrono vive en el `job_id`).
- Es replicable en horizontal (N réplicas detrás de un balanceador).

---

## Qué resuelve el SDK por nosotros

No está en nuestro código, pero conviene saberlo: el SDK `mcp` 2.x implementa
automáticamente varias exigencias de la spec:

- Negociación de versión de protocolo (`MCP-Protocol-Version`).
- Cabeceras `Mcp-Method` / `Mcp-Name` y su validación contra el body.
- Rechazo de métodos desconocidos con `-32601` y 404.
- Códigos JSON-RPC de errores de protocolo (`-32700`, `-32602`, …).
- Límite de tamaño del body de petición (por defecto 4 MiB).
- Emisión de `serverInfo` con `name`/`version` en cada respuesta.

---

## Pendientes (no cubiertos aún)

Para ser honestos, esto **no** lo cumple todavía (documentado, no olvidado):

1. **Autenticación** — la spec recomienda (`SHOULD`) autenticar. Hoy el MCP está
   abierto; es la fase 6 del plan (Bearer por cliente + reenvío al core).
2. **Rate limiting** — la spec lo lista como `MUST` en seguridad; se resuelve en
   el gateway (Nginx) + aplicación.
3. **`outputSchema` / `structuredContent`** — ya implementado en las cuatro
   tools mediante modelos de salida.
4. **`title`** en las tools — opcional, cosmético.

---

## Fuentes

1. [MCP 2026-07-28 — Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
2. [MCP 2026-07-28 — Resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)
3. [MCP 2026-07-28 — Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
4. [MCP Python SDK (docs)](https://py.sdk.modelcontextprotocol.io/)
5. Código fuente del SDK instalado: `mcp/server/transport_security.py`,
   `mcp/server/mcpserver/server.py`, `mcp.types.ToolAnnotations`
6. `Whisper-MCP-diseno.md` (contrato funcional de referencia)
