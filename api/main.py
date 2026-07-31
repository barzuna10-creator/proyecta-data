from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import sqlite3

from db import BASE_DATOS
from api.routers import proyectos
from busqueda import buscar_fts as _buscar_fts_motor
from familias import analizar_nombre as _analizar_nombre_familia
from reranking import reordenar as _reordenar_resultados
from capa_intencion import detectar_concepto, PALABRAS_CONTEXTO_NORMALIZADAS

app = FastAPI(
    title="Proyecta CR API",
    version="1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://proyecta-beta.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(proyectos.router)

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

    productos = []

    for r in resultados:
        item = {
            "nombre": r["nombre"],
            "precio": r["precio"],
            "categoria": r["categoria"],
            "proveedor": r["proveedor"],
            "id_proveedor": r["id_proveedor"],
            "url_producto": r["url_producto"],
            "url_imagen": r["url_imagen"],
        }

        # Agrupación de variantes de presentación (solo Pinturas por ahora,
        # ver familias.py) -- familia_id viene NULL para todo lo demás.
        if r["familia_id"] is not None:
            item["familia_id"] = r["familia_id"]
            item["nombre_familia"] = r["nombre_familia"]
            item["presentacion"] = _analizar_nombre_familia(r["nombre"])[2]

        productos.append(item)

    return productos


@app.get("/buscar")
def buscar(q: str):
    if USE_FTS_SEARCH:
        return _buscar_fts(q)

    return _buscar_like(q)
