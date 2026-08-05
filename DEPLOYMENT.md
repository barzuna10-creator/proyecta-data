# Despliegue

Este documento no existía antes de `PRODUCTION_READINESS_REVIEW.md`
(hallazgo H1: "no existe ningún Dockerfile, docker-compose, ni
documentación de despliegue en ningún repo"). Documenta cómo correr
Proyecta en producción de verdad, no con `uvicorn --reload` / `next dev`.
No agrega contenedores ni infraestructura nueva -- solo el comando real y
las variables de entorno que ya existen en el código pero antes solo
tenían un valor hardcodeado.

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

No hay ningún respaldo automático corriendo hoy (`PRODUCTION_READINESS_REVIEW.md`,
hallazgo B1). `database/respaldar_db.py` existe y funciona (`sqlite3
.backup`, seguro para copiar la base mientras la API sigue corriendo), pero
**alguien tiene que programarlo** -- este repo no configura ningún cron por
sí mismo. En la plataforma de despliegue real, agregar un job periódico
(diario como mínimo) que corra:

```bash
PYTHONPATH=. .venv/bin/python3 database/respaldar_db.py
```

Y, crítico, **sacar esos respaldos del propio servidor** (a un bucket, a
otro disco) -- un respaldo que vive en el mismo disco que la base original
no protege contra una falla de disco.

### Dependencias

`requirements.txt` mezcla el stack real de la API (FastAPI/uvicorn/pydantic)
con el stack completo de los crawlers (Scrapy/Twisted/Playwright) -- un
contenedor de producción de la API instala todo eso sin necesitarlo
(`PRODUCTION_READINESS_REVIEW.md`, hallazgo A6). No se separó en esta
pasada por ser un cambio de proceso de build, no una corrección de código;
queda documentado para la siguiente.

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

Documentado en detalle en `PRODUCTION_READINESS_REVIEW.md` -- no se
resuelve acá porque cada uno requiere una decisión de producto o de
infraestructura real, no un comando:

- **Dónde correr esto de verdad** (qué proveedor, qué disco persistente,
  qué dominio). El fallback de `NEXT_PUBLIC_API_URL` sugiere Render, pero
  eso nunca se documentó como una decisión explícita en ningún lado del
  repo -- confirmarlo antes de asumirlo.
- **Autenticación real de usuarios** (hallazgo P0 #1 del PRR) -- ningún
  comando de despliegue lo arregla, es trabajo de producto.
- **Que el disco de producción sobreviva un redeploy** -- depende
  enteramente de la plataforma elegida y de cómo esté configurada; no
  verificado en esta pasada.
