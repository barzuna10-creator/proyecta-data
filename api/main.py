from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import sqlite3

from db import BASE_DATOS
from api.routers import proyectos, sistemas_constructivos
from api.identidad import obtener_propietario_id
from busqueda import buscar_fts as _buscar_fts_motor
from familias import analizar_nombre as _analizar_nombre_familia
from reranking import reordenar as _reordenar_resultados
from capa_intencion import detectar_concepto, PALABRAS_CONTEXTO_NORMALIZADAS
from similares import obtener_similares as _obtener_similares_motor, LIMITE_DEFECTO as _LIMITE_SIMILARES_DEFECTO
from presupuestos import calcular_presupuesto as _calcular_presupuesto_motor
from especificaciones import unidad_comercial as _unidad_comercial

app = FastAPI(
    title="Proyecta CR API",
    version="1.0"
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
app.include_router(proyectos.router)
app.include_router(sistemas_constructivos.router)

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


def _buscar_like(q):
    conexion = sqlite3.connect(BASE_DATOS)
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
