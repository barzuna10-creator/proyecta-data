from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import json
import os
import threading
import time

from db import conectar
from api.repositorio_proyectos import apagar_executor_planos, recuperar_analisis_interrumpidos
from api.routers import auth as auth_router, feedback as feedback_router, metricas as metricas_router, proyectos, sistemas_constructivos
from api.identidad import obtener_propietario_id
from api.observabilidad import logger as _logger, middleware_logging as _middleware_logging
from id_producto import id_producto_a_url as _id_producto_a_url
from busqueda import buscar_fts as _buscar_fts_motor
from familias import analizar_nombre as _analizar_nombre_familia
from reranking import reordenar as _reordenar_resultados
from capa_intencion import detectar_concepto, PALABRAS_CONTEXTO_NORMALIZADAS
from similares import obtener_similares as _obtener_similares_motor, LIMITE_DEFECTO as _LIMITE_SIMILARES_DEFECTO
from presupuestos import calcular_presupuesto as _calcular_presupuesto_motor
from especificaciones import unidad_comercial as _unidad_comercial
from database.respaldar_db import CODIGO_LOCK_OCUPADO, respaldar as _respaldar_db
from database.migraciones import aplicar_migraciones_pendientes as _aplicar_migraciones_pendientes

# Investigación "no such table: usuarios" en producción: nada, en ningún
# punto del despliegue, ejecutaba las migraciones de database/agregar_*.py
# contra la base real (ver database/migraciones.py para el diseño
# completo -- registro explícito, reclamo atómico entre los --workers,
# libera el reclamo si una migración falla).
#
# Investigación posterior, "database is locked" durante el arranque:
# antes, este hilo de migraciones y el hilo de respaldo (más abajo)
# arrancaban EN PARALELO, los dos desde su propio on_event("startup") --
# dos escritores concurrentes contra la misma base real, justo en el
# momento de más escrituras de todo el ciclo de vida del proceso. Ahora
# es un solo hilo de fondo: primero terminan las migraciones, RECIÉN
# DESPUÉS arranca el bucle de respaldo -- nunca compiten por la base al
# mismo tiempo. (El otro cambio de esa misma investigación fue que las 8
# migraciones y el respaldo pasaron de sqlite3.connect() crudo a
# db.conectar(), que ya trae busy_timeout -- ver database/migraciones.py
# y database/respaldar_db.py. Ese es el fix real contra el bloqueo en sí;
# esta secuencia es una reducción adicional de cuándo puede competir por
# el lock, no un reemplazo del busy_timeout.)
#
# BETA_1.0_CHECKLIST.md, hallazgo 1.4/5.1: el script de respaldo
# (database/respaldar_db.py) ya existía y funcionaba, pero nada lo
# ejecutaba -- "una promesa sin cumplir". En vez de depender de que
# alguien configure un cron en la plataforma de despliegue (algo que este
# repo no puede hacer por sí mismo), el respaldo se agenda desde DENTRO
# del propio proceso -- respalda apenas terminan las migraciones, y
# después cada INTERVALO_RESPALDO_SEGUNDOS. Un solo hilo en segundo plano
# (daemon=True, no bloquea el apagado del proceso), no el loop de asyncio
# -- tanto las migraciones como .backup() de sqlite3 son llamadas
# bloqueantes.
INTERVALO_RESPALDO_SEGUNDOS = 6 * 60 * 60  # cada 6 horas


def _bucle_arranque_en_segundo_plano():
    _aplicar_migraciones_pendientes()
    # Mission #002 (Plan Processing Stability): después de migraciones (la
    # columna plano_estado tiene que existir ya) y antes del loop de
    # respaldo -- cualquier plano_estado='procesando' en este punto es
    # huérfano (el proceso anterior que lo estaba procesando ya no existe,
    # ver api/repositorio_proyectos.py::recuperar_analisis_interrumpidos).
    # try/except a propósito, mismo criterio que el try/except del loop de
    # respaldo más abajo: un fallo acá (ej. las migraciones de proyectos
    # todavía no terminaron en este proceso) nunca debe impedir que el
    # resto del arranque -- en particular, el propio loop de respaldo --
    # siga corriendo.
    try:
        recuperar_analisis_interrumpidos()
    except Exception:
        _logger.exception("Falló recuperar_analisis_interrumpidos() al arrancar -- el arranque continúa igual.")
    while True:
        try:
            resultado = _respaldar_db()
            if resultado == CODIGO_LOCK_OCUPADO:
                _logger.warning(
                    "RESPALDO estado=omitido codigo=%s causa=lock_ocupado",
                    resultado,
                )
            elif resultado != 0:
                _logger.error(
                    "RESPALDO estado=fallido codigo=%s",
                    resultado,
                )
        except Exception:
            _logger.exception("Falló el respaldo automático de la base de datos.")
        time.sleep(INTERVALO_RESPALDO_SEGUNDOS)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Reemplaza los antiguos @app.on_event("startup"/"shutdown") --
    # deprecados en FastAPI reciente a favor de este único context
    # manager (ver RELEASE_CANDIDATE.md). Mismo comportamiento exacto,
    # solo el mecanismo de registro cambia: todo lo de antes de `yield`
    # corría en "startup", todo lo de después corría en "shutdown".
    threading.Thread(target=_bucle_arranque_en_segundo_plano, daemon=True).start()
    yield
    # Sin esto, el proceso worker que ProcessPoolExecutor deja abierto
    # (ver api/repositorio_proyectos.py) sobrevive al proceso principal en
    # un shutdown limpio -- inofensivo en producción (el orquestador mata
    # el grupo de procesos entero), pero en desarrollo con --reload cada
    # recarga dejaría un proceso huérfano más.
    apagar_executor_planos()


app = FastAPI(
    title="Proyecta CR API",
    version="1.0",
    lifespan=_lifespan,
)

# PRODUCTION_READINESS_REVIEW.md, hallazgo A7/H1: orígenes por defecto
# iguales a los de siempre -- CORS_ORIGINS solo los reemplaza si está
# seteada (coma-separados), para no tener que editar código y redesplegar
# cada vez que se agrega un origen nuevo (ej. una nueva URL de preview).
_ORIGENES_CORS_DEFECTO = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://proyecta-beta.vercel.app",
]
_origenes_cors_env = os.environ.get("CORS_ORIGINS")
ORIGENES_CORS = (
    [origen.strip() for origen in _origenes_cors_env.split(",") if origen.strip()]
    if _origenes_cors_env
    else _ORIGENES_CORS_DEFECTO
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGENES_CORS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# BETA_1.0_CHECKLIST.md, hallazgos 1.1/8.2 (logging estructurado) y 8.1
# (analítica mínima) -- ver api/observabilidad.py para por qué un solo
# mecanismo resuelve ambos.
app.middleware("http")(_middleware_logging)

app.include_router(auth_router.router)
app.include_router(feedback_router.router)
app.include_router(proyectos.router)
app.include_router(sistemas_constructivos.router)
app.include_router(metricas_router.router)

# Etapa 2 del motor de búsqueda (ver busqueda.py): alterna entre el buscador
# actual (LIKE + precio) y FTS5. Cambiar a False vuelve al buscador anterior
# de inmediato, sin tocar nada más.
USE_FTS_SEARCH = True

# Etapa 3 (ver reranking.py): re-ranking en Python sobre un candidate set
# amplio de FTS5, antes de aplicar el límite final de resultados. Bandera
# independiente de USE_FTS_SEARCH -- se puede apagar solo el re-ranking y
# quedarse con el orden crudo de bm25 sin tocar nada más.
USE_RERANKING = True

# Experimento controlado (ver DISENO_MINIMO_CONCEPTOS.md): antes de construir
# la consulta FTS5, revisa si la búsqueda activa uno de los 5 conceptos
# definidos en conceptos_intencion.py. Si no activa ninguno, no cambia nada.
# Bandera independiente de las anteriores -- apagarla vuelve al buscador
# exactamente como estaba antes de este experimento, sin tocar nada más.
USE_INTENT_LAYER = False

# Presupuestos inteligentes (ver ARQUITECTURA_PRESUPUESTOS_INTELIGENTES.md y
# PRESUPUESTOS_INTELIGENTES.md): calcula ahorro comparando cada renglón de
# un proyecto contra alternativas de otros proveedores, pero solo cuenta el
# ahorro cuando la alternativa está CONFIRMADA como comparable (ver
# presupuestos.py/especificaciones.py) -- nunca sobre una relación débil.
# Bandera independiente de las demás -- apagarla hace que el endpoint
# devuelva 404 sin tocar nada del resto de proyectos.
USE_SMART_BUDGETS = True

LIMITE_RESULTADOS = 50
LIMITE_CANDIDATOS_RERANKING = 300


@app.get("/")
def inicio():
    return {
        "mensaje": "Bienvenido a Proyecta CR"
    }


# MASTER_ROADMAP.md, NOW #1: antes de esto, "/" respondía 200 sin tocar la
# base de datos -- un monitor de uptime (o el propio Render) reportaba
# "sano" con la base caída o con el catálogo vacío (PRODUCTION_READINESS_
# REVIEW.md, hallazgo A5). Los dos chequeos que importan para decidir si el
# backend puede servir tráfico de verdad: ¿la base responde? ¿hay productos
# que buscar? Cualquier otra cosa (desglose por proveedor, precios
# inválidos, paridad del índice FTS) ya vive en herramientas_desarrollo/
# verificar_catalogo.py -- un diagnóstico para un humano, no algo que un
# probe de salud dispare en cada llamada.
#
# El cuerpo de la respuesta nunca incluye la excepción real, la ruta de la
# base de datos, ni ningún dato de producto -- solo estados fijos
# ("ok"/"error"/"empty"), para que un fallo de conectividad no filtre
# infraestructura interna a quien golpee el endpoint público. El detalle
# real de la excepción sí queda en el log del servidor (_logger), para
# poder diagnosticar sin exponerlo por HTTP.
@app.get("/health")
def salud():
    estado_bd = "ok"
    estado_catalogo = "unknown"

    try:
        conexion = conectar()
        try:
            conexion.execute("SELECT 1")
            fila = conexion.execute("SELECT COUNT(*) AS total FROM productos").fetchone()
            estado_catalogo = "ok" if fila["total"] > 0 else "empty"
        finally:
            conexion.close()
    except Exception:
        _logger.exception("HEALTH_CHECK estado=fallido causa=conexion_o_consulta")
        estado_bd = "error"

    saludable = estado_bd == "ok" and estado_catalogo == "ok"

    cuerpo = {
        "status": "ok" if saludable else "degraded",
        "checks": {"database": estado_bd, "catalog": estado_catalogo},
    }

    return JSONResponse(content=cuerpo, status_code=200 if saludable else 503)


# MASTER_ROADMAP.md, NOW #1: identidad de despliegue, deliberadamente
# separada de /health -- un backend puede estar "sano" (base conectada,
# catálogo con productos) y aun así correr un commit viejo o equivocado; y
# viceversa, saber qué commit corre no dice nada sobre si puede servir
# tráfico. Nunca toca la base -- debe poder responder incluso cuando
# /health reporta degradado. RENDER_GIT_COMMIT es la variable que Render
# expone en runtime con el SHA del commit desplegado (documentado por
# Render); "unknown" es la respuesta segura y explícita cuando no está
# seteada (ej. en local o en otra plataforma), en vez de inventar un valor.
@app.get("/version")
def version():
    return {
        "version": app.version,
        "commit": os.environ.get("RENDER_GIT_COMMIT", "unknown"),
    }


def _buscar_like(q):
    conexion = conectar()
    cursor = conexion.cursor()

    cursor.execute(
        """
        SELECT
            nombre,
            precio,
            categoria,
            proveedor,
            id_proveedor,
            url_producto,
            url_imagen
        FROM productos
        WHERE nombre LIKE ?
        ORDER BY precio ASC
        LIMIT 50
        """,
        (f"%{q}%",)
    )

    resultados = cursor.fetchall()

    conexion.close()

    productos = []

    for r in resultados:
        productos.append({
            "nombre": r[0],
            "precio": r[1],
            "categoria": r[2],
            "proveedor": r[3],
            "id_proveedor": r[4],
            "url_producto": r[5],
            "url_imagen": r[6]
        })

    return productos


def _serializar_producto(r):
    """Convierte una fila de producto (de /buscar o de similares.py) al
    dict que espera el frontend -- cobertura de estos campos varía mucho
    por proveedor, se omiten si vienen NULL en vez de mandar el campo
    vacío."""

    item = {
        "nombre": r["nombre"],
        "precio": r["precio"],
        "categoria": r["categoria"],
        "proveedor": r["proveedor"],
        "id_proveedor": r["id_proveedor"],
        "url_producto": r["url_producto"],
        "url_imagen": r["url_imagen"],
    }

    if r["marca"] is not None:
        item["marca"] = r["marca"]
    if r["sku"] is not None:
        item["sku"] = r["sku"]
    if r["subcategoria"] is not None:
        item["subcategoria"] = r["subcategoria"]
    if r["descripcion"] is not None:
        item["descripcion"] = r["descripcion"]
    if r["peso"] is not None:
        item["peso"] = r["peso"]
    if r["imagenes_adicionales"] is not None:
        try:
            item["imagenes_adicionales"] = json.loads(r["imagenes_adicionales"])
        except ValueError:
            pass

    # Agrupación de variantes de presentación (solo Pinturas por ahora,
    # ver familias.py) -- familia_id viene NULL para todo lo demás.
    if r["familia_id"] is not None:
        item["familia_id"] = r["familia_id"]
        item["nombre_familia"] = r["nombre_familia"]
        item["presentacion"] = _analizar_nombre_familia(r["nombre"])[2]

    # Unidad de venta legible (Galón, 25 kg, 2.08 m²...) para no depender
    # de un "c/u" ambiguo en la interfaz -- ver PRUEBA_INGENIERO_BANO.md,
    # hallazgo #2. None cuando no hay señal confiable en el nombre; el
    # frontend cae a "c/u" en ese caso, nunca inventa una unidad.
    unidad = _unidad_comercial(r["nombre"], r["categoria"])
    if unidad is not None:
        item["unidad_comercial"] = unidad

    return item


def _buscar_fts(q):
    categorias_permitidas = None
    exclusiones = None
    palabras_a_ignorar = None

    if USE_INTENT_LAYER:
        concepto = detectar_concepto(q)
        if concepto is not None:
            categorias_permitidas = concepto["categorias_permitidas"]
            exclusiones = concepto["exclusiones"]
            palabras_a_ignorar = PALABRAS_CONTEXTO_NORMALIZADAS

    if USE_RERANKING:
        candidatos = _buscar_fts_motor(
            q,
            limite=LIMITE_CANDIDATOS_RERANKING,
            categorias_permitidas=categorias_permitidas,
            exclusiones=exclusiones,
            palabras_a_ignorar=palabras_a_ignorar,
        )
        resultados = _reordenar_resultados(candidatos, q, limite=LIMITE_RESULTADOS)
    else:
        resultados = _buscar_fts_motor(
            q,
            limite=LIMITE_RESULTADOS,
            categorias_permitidas=categorias_permitidas,
            exclusiones=exclusiones,
            palabras_a_ignorar=palabras_a_ignorar,
        )

    return [_serializar_producto(r) for r in resultados]


@app.get("/buscar")
def buscar(q: str):
    if USE_FTS_SEARCH:
        return _buscar_fts(q)

    return _buscar_like(q)


@app.get("/productos/similares")
def productos_similares(proveedor: str, id_proveedor: str, limite: int = _LIMITE_SIMILARES_DEFECTO):
    # Tope defensivo -- este endpoint es para la sección de la página de
    # detalle (hasta 6 por diseño), no un listado general.
    limite = max(1, min(limite, 12))

    resultados = _obtener_similares_motor(proveedor, id_proveedor, limite=limite)

    return [_serializar_producto(r) for r in resultados]


# RELEASE_CANDIDATE.md: antes /producto/{id} en el frontend solo se podía
# resolver desde sessionStorage (poblado al navegar desde resultados de
# búsqueda) -- un link compartido, recargado, o abierto en pestaña nueva
# siempre mostraba "Producto no disponible", indistinguible de un
# producto realmente eliminado. Este endpoint permite reconstruir
# CUALQUIER url /producto/{id} válida directamente desde el catálogo,
# sin depender de qué haya (o no) en el navegador de quien lo abre. Va
# DESPUÉS de /productos/similares a propósito -- FastAPI resuelve rutas
# en el orden en que se registran, y una ruta dinámica /productos/{id}
# antes de la estática /productos/similares capturaría "similares" como
# si fuera un id.
@app.get("/productos/{id}")
def producto_por_id(id: str):
    url_producto = _id_producto_a_url(id)
    if url_producto is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    conexion = conectar()
    fila = conexion.execute(
        """
        SELECT
            p.nombre, p.precio, p.categoria, p.proveedor,
            p.id_proveedor, p.url_producto, p.url_imagen,
            p.marca, p.sku, p.subcategoria, p.descripcion,
            p.peso, p.imagenes_adicionales,
            p.familia_id, f.nombre_familia
        FROM productos p
        LEFT JOIN familias_producto f ON f.id = p.familia_id
        WHERE p.url_producto = ?
        """,
        (url_producto,),
    ).fetchone()
    conexion.close()

    if not fila:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    return _serializar_producto(fila)


@app.get("/proyectos/{proyecto_id}/presupuesto")
def presupuesto_de_proyecto(
    proyecto_id: int,
    propietario_id: str = Depends(obtener_propietario_id),
):
    if not USE_SMART_BUDGETS:
        raise HTTPException(status_code=404, detail="Not Found")

    presupuesto = _calcular_presupuesto_motor(proyecto_id, propietario_id)

    if presupuesto is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    return presupuesto
