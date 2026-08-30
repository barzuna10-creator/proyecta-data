# Despliegue

Este documento no existía antes de `PRODUCTION_READINESS_REVIEW.md`
(hallazgo H1: "no existe ningún Dockerfile, docker-compose, ni
documentación de despliegue en ningún repo"). Documenta cómo correr
Proyecta en producción de verdad, no con `uvicorn --reload` / `next dev`.
No agrega contenedores ni infraestructura nueva -- solo el comando real y
las variables de entorno que ya existen en el código pero antes solo
tenían un valor hardcodeado.

**Actualizado en RELEASE_CANDIDATE_1.md** (ver BETA_1.0_CHECKLIST.md,
hallazgo 6.1): ahora existe `render.yaml` en la raíz del repo con esta
misma configuración, versionada -- ver la sección "Aplicar render.yaml"
más abajo para el paso que todavía requiere acción humana (aplicarlo en
el dashboard real de Render).

## Backend (`proyecta-data`)

**Nunca usar `--reload` en producción** -- recarga el proceso ante
cualquier cambio de archivo y no es lo que se quiere en un servidor real.

```bash
PYTHONPATH=. .venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

`--workers` levanta varios procesos (no hilos) para atender más requests
concurrentes -- ver `PRODUCTION_READINESS_REVIEW.md`, hallazgo A4/H4: cada
endpoint es síncrono y corre en el threadpool de un solo proceso, así que
sin `--workers` toda la capacidad de la API está limitada a un único
proceso Python. Ajustar el número según los núcleos disponibles del
servidor (regla general: `2 × núcleos + 1`).

### Variables de entorno

Ninguna es obligatoria -- si no se setea ninguna, el comportamiento es
idéntico al que ya existía antes de este documento.

| Variable | Qué controla | Valor por defecto |
|---|---|---|
| `DATABASE_PATH` | Ruta al archivo SQLite (`db.py`) | `database/proyecta.db` |
| `CORS_ORIGINS` | Orígenes permitidos, separados por coma (`api/main.py`) | `http://localhost:3000,http://127.0.0.1:3000,https://proyecta-beta.vercel.app` |

Ejemplo para un despliegue con un disco persistente montado aparte:

```bash
DATABASE_PATH=/data/proyecta.db \
CORS_ORIGINS=https://mi-frontend-real.com \
PYTHONPATH=. .venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Respaldo de la base de datos

**Ya corre solo.** `api/main.py` agenda `database/respaldar_db.py` desde
un hilo en segundo plano al arrancar el proceso (cada 6 horas, y una vez
al iniciar) -- no depende de que alguien configure un cron aparte en la
plataforma. Los respaldos se guardan junto a la base real (mismo
directorio que `DATABASE_PATH`, subcarpeta `respaldos/`) -- si
`DATABASE_PATH=/data/proyecta.db`, quedan en `/data/respaldos/`, en el
mismo disco persistente, no en el checkout efímero del repo. Sigue siendo
crítico **sacar esos respaldos del propio servidor** (a un bucket, a otro
disco) -- un respaldo que vive en el mismo disco que la base original no
protege contra una falla de disco completa. Eso sigue siendo trabajo
manual/de infraestructura, no algo que el proceso de la API pueda hacer
por sí mismo sin credenciales de almacenamiento externo.

### Migraciones de base de datos

**Ya corren solas.** Causa raíz de un incidente real: el código nuevo se
desplegaba, pero ninguna migración de `database/agregar_*.py` se ejecutaba
contra la base real -- siempre se corrieron a mano, en local. Un
`registro` de usuario fallaba en producción con
`sqlite3.OperationalError: no such table: usuarios` porque
`agregar_autenticacion.py` nunca había corrido ahí.

Ahora `api/main.py` agenda `database/migraciones.py` desde un hilo en
segundo plano al arrancar (mismo patrón que el respaldo automático, arriba)
-- cada migración se aplica una sola vez, con seguimiento en la tabla
`migraciones_aplicadas`, sin importar cuántos `--workers` corran en
paralelo. No hace falta correr nada a mano tras un deploy. Ver
`database/migraciones.py` para el diseño completo y la causa raíz.

### Aplicar `render.yaml`

El archivo ya existe, versionado, en la raíz del repo -- pero **tenerlo
en git no alcanza por sí solo**: alguien con acceso al dashboard de
Render tiene que aplicarlo (Render → New → Blueprint, apuntando a este
repo) o migrar el servicio ya existente para que use el disco persistente
declarado ahí (`/data`, con `DATABASE_PATH=/data/proyecta.db`).

**Si el servicio de Render ya existe y corre hoy sin un disco
persistente**, migrar requiere un paso manual antes de cambiar
`DATABASE_PATH`: copiar el `database/proyecta.db` actual al nuevo disco
una sola vez (por ejemplo, por SSH shell de Render, o incluyendo la copia
en el primer deploy), para no arrancar con una base vacía y perder el
historial ya acumulado.

### Dependencias

`requirements.txt` mezcla el stack real de la API (FastAPI/uvicorn/pydantic)
con el stack completo de los crawlers (Scrapy/Twisted/Playwright) -- un
contenedor de producción de la API instala todo eso sin necesitarlo
(`PRODUCTION_READINESS_REVIEW.md`, hallazgo A6). No se separó en esta
pasada por ser un cambio de proceso de build, no una corrección de código;
queda documentado para la siguiente.

## Jarvis Control Plane (macOS/launchd)

**Jarvis God Mode M0.** Antes de este milestone, `jarvis/control_plane_server.py`
no tenía ninguna documentación de despliegue -- se arrancaba a mano en
sesiones locales interactivas. Este es el primer supervisor de proceso
real, y está deliberadamente acotado: **local macOS únicamente, vía
`launchd`. No systemd. No servidor remoto. No deploy administrado.**

El comando real (idéntico al que ya se ejecutaba a mano; nada nuevo aquí):

```bash
PYTHONPATH=. python3 -m jarvis.control_plane_server
```

### Variables de entorno

Ninguna variable nueva es obligatoria más allá de las que el módulo ya
requería antes de M0 -- `CONTROL_PLANE_TOKEN` y `CONTROL_PLANE_STORE_ROOT`.
El resto sigue siendo opcional, con el mismo comportamiento "sin setear
ninguna" que documenta la sección de Backend arriba.

| Variable | Qué controla | Obligatoria |
|---|---|---|
| `CONTROL_PLANE_TOKEN` | Bearer token de autenticación (mínimo 32 caracteres) | Sí |
| `CONTROL_PLANE_STORE_ROOT` | Raíz del store de Jarvis (drafts, etc.) | Sí |
| `CONTROL_PLANE_HOST` | Host de bind -- debe ser loopback, se rechaza cualquier otro | No (`127.0.0.1`) |
| `CONTROL_PLANE_PORT` | Puerto de escucha | No (`4318`) |
| `CONTROL_PLANE_KNOWLEDGE_STORE_ROOT` | Mission 005 -- citas de conocimiento autorizado | No |
| `CONTROL_PLANE_ZENTRA_REPOSITORY_ROOT` | Mission 005 -- resolución de freshness | No |
| `CONTROL_PLANE_ZENTRA_BACKEND_ROOT` / `_FRONTEND_ROOT` | Trusted Zentra Context V1 | No |
| `CONTROL_PLANE_MISSION_REPOSITORY_ROOT` | Mission 006 -- habilita adapters reales de Emilio/Emma | No |

### Supervisión de proceso (launchd)

`ops/jarvis-control-plane.plist` es un `LaunchAgent` (corre como el
usuario logueado, nunca como root -- el Control Plane sólo hace bind a
loopback). No contiene lógica de negocio ni conoce Chugel/Mission
Records: únicamente el comando y la política de reinicio del sistema
operativo (`KeepAlive` + `RunAtLoad`).

```bash
cp ops/jarvis-control-plane.plist ~/Library/LaunchAgents/
# Editar el path absoluto del checkout y CONTROL_PLANE_TOKEN dentro del
# archivo antes de instalar -- ver los comentarios del propio .plist.
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/jarvis-control-plane.plist
```

Verificar que `launchd` lo está supervisando de verdad:

```bash
launchctl list | grep com.zentra.jarvis.control-plane
```

Desinstalar:

```bash
launchctl bootout gui/$(id -u)/com.zentra.jarvis.control-plane
rm ~/Library/LaunchAgents/jarvis-control-plane.plist
```

### Liveness

`GET /v1/health` no requiere autenticación y responde únicamente
`{"status": "ok"}` -- nunca expone misiones, drafts, agentes, tokens,
configuración, paths del filesystem, estado de Git, datos de negocio, ni
Trusted Zentra Context. Existe para que un supervisor externo simple
(launchd, o cualquier chequeo manual) pueda confirmar que el proceso HTTP
responde, sin tocar Chugel ni el store.

### Riesgo abierto (no resuelto por M0)

`KeepAlive` reinicia el proceso incondicionalmente ante cualquier salida,
sin backoff -- un proceso que crashea en loop se reinicia en loop en vez
de escalar a José. Documentado como riesgo abierto en el M0 Implementation
Readiness Review; fuera de alcance de este milestone.

## Frontend (`proyecta-web`)

```bash
npm run build
npm run start
```

`npm run start` sirve el build de producción (`.next/`) -- `npm run dev`
nunca debe usarse fuera de desarrollo local.

### Variables de entorno

| Variable | Qué controla | Valor por defecto |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | URL del backend que consume el frontend (`app/lib/config.ts`) | `https://proyecta-data.onrender.com` |

## Lo que este documento NO resuelve

- **Que alguien aplique `render.yaml` de verdad contra el servicio real**
  -- ver "Aplicar render.yaml" arriba. El archivo versionado reduce el
  riesgo de "nadie sabe cómo está configurado" a "hay una receta
  concreta que aplicar", pero no reemplaza confirmar que se aplicó.
- **Backups fuera del propio servidor** (a un bucket externo, por
  ejemplo) -- el respaldo automático (ver arriba) protege contra un error
  de la aplicación o una migración mala, no contra una falla del disco
  completo de Render.
- Autenticación real **ya existe** (`api/auth.py`, `POST /auth/registro`,
  `POST /auth/login`) -- ver `RELEASE_CANDIDATE_1.md`.
