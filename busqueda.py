"""Motor de búsqueda basado en FTS5 (Etapa 1: índice + normalización, sin re-ranking todavía).

Este módulo es independiente del endpoint /buscar actual — construye y consulta
el índice FTS5, pero nada en api/main.py lo usa todavía. Sirve para comparar
contra el buscador actual antes de decidir si reemplazarlo.
"""

import re
import sqlite3
import unicodedata

from db import conectar

# Abreviaturas y variantes que el catálogo y los usuarios escriben distinto
# para la misma unidad. Se aplica tanto al indexar como al consultar.
SINONIMOS = {
    "metros": "m",
    "metro": "m",
    "mts": "m",
    "mt": "m",
    "pulgadas": "pulg",
    "pulgada": "pulg",
    "plg": "pulg",
    "centimetros": "cm",
    "centimetro": "cm",
    "milimetros": "mm",
    "milimetro": "mm",
    "kilogramos": "kg",
    "kilogramo": "kg",
    "kilos": "kg",
    "kilo": "kg",
    "galones": "galon",
    "libras": "lb",
    "libra": "lb",
    # detectados en la evaluación de QA: abreviaturas eléctricas que el
    # catálogo escribe como una sola letra (100 A, 9 W, 120 V).
    "amperios": "a",
    "amperaje": "a",
    "amp": "a",
    "amps": "a",
    "watts": "w",
    "vatios": "w",
    "voltios": "v",
    "volts": "v",
    "volt": "v",
}

# Deliberadamente corta: tokens cortos como "m", "cm", "kg" NUNCA deben tratarse
# como ruido, así que no se usa una lista genérica de stopwords en español.
STOPWORDS = {"de", "para", "con", "el", "la", "los", "las", "un", "una", "y", "o", "del", "en"}

PESO_NOMBRE = 10.0
PESO_CATEGORIA = 2.0
PESO_SUBCATEGORIA = 1.0

# Variantes de género/número para adjetivos frecuentes del catálogo. Cada
# proveedor escribe estas palabras de forma distinta (EPA usa "blanco" en vez
# de "blanca", por ejemplo) y FTS5 no hace stemming en español, así que sin
# esto una búsqueda como "pintura blanca" no encuentra productos indexados
# como "blanco". Solo se aplica a la consulta del usuario, nunca al índice:
# el nombre original de cada producto queda intacto.
GRUPOS_MORFOLOGICOS = [
    {"blanco", "blanca", "blancos", "blancas"},
    {"rojo", "roja", "rojos", "rojas"},
    {"negro", "negra", "negros", "negras"},
    {"gris", "grises"},
    {"pequeno", "pequena", "pequenos", "pequenas"},
    {"grande", "grandes"},
]

# A diferencia de GRUPOS_MORFOLOGICOS (variantes de género/número de LA
# MISMA palabra), estos son términos DISTINTOS que nombran el mismo
# material -- confirmados contra el catálogo real, no supuestos (ver
# BUSCADOR_AUDITORIA.md):
#
# - "bloque" (español) nunca encontraba los bloques de concreto reales del
#   catálogo, porque EPA los lista como "Block" (préstamo del inglés, uso
#   común en construcción en Costa Rica) -- "Block PC clase A 20 x 20 x 40
#   cm" y ~15 productos más eran invisibles a la búsqueda "bloque".
# - "perlín" (término coloquial costarricense para el perfil C/Z de acero
#   usado en techos) devolvía cero resultados -- el catálogo lo llama
#   "Perfil C" (EPA) o "Perfil" en general (El Lagar), nunca "perlín".
GRUPOS_VOCABULARIO_MATERIALES = [
    {"bloque", "bloques", "block", "blocks"},
    {"perlin", "perlines", "perfil", "perfiles"},
]

VARIANTE_A_GRUPO = {
    variante: grupo
    for grupos in (GRUPOS_MORFOLOGICOS, GRUPOS_VOCABULARIO_MATERIALES)
    for grupo in grupos
    for variante in grupo
}


def normalizar_texto(texto):
    if not texto:
        return ""

    texto = texto.lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(caracter for caracter in texto if unicodedata.category(caracter) != "Mn")

    # "número 8", "num 8", "no. 8" -> "8": el catálogo siempre escribe "#8"
    # (que ya se reduce a "8" más abajo), nunca la palabra "número". Solo se
    # quita la palabra cuando precede a un dígito, para no tocar nada más.
    texto = re.sub(r"\b(numero|num|no)\.?\s+(?=\d)", "", texto)
    # "60x60" -> "60 x 60": el catálogo separa las medidas con espacios: sin
    # esto, una medida escrita junta nunca coincide con la del catálogo.
    texto = re.sub(r"(\d)\s*x\s*(\d)", r"\1 x \2", texto)

    # separadores como "/" en "T/PINTURA" o "-" cuentan como límite de palabra
    texto = re.sub(r"[/\-_,;:]+", " ", texto)
    # comilla doble o símbolo ″ como marca de pulgada
    texto = re.sub(r'["″]', " pulg ", texto)
    texto = re.sub(r"[^a-z0-9. ]+", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()

    return texto


def _variantes_numero(token):
    """Variantes de plural/singular por regla general (no diccionario curado):
    complementa GRUPOS_MORFOLOGICOS para los casos de número que no están
    explícitamente en la lista. Solo aplica a tokens alfabéticos de más de
    3 caracteres, para no tocar unidades cortas como "m", "cm", "kg"."""

    variantes = {token}

    if len(token) > 3 and token.isalpha():
        if token.endswith("s"):
            variantes.add(token[:-1])
            if token.endswith("es") and len(token) > 5:
                variantes.add(token[:-2])
        else:
            variantes.add(token + "s")

    return variantes


def tokenizar(texto_normalizado):
    tokens = texto_normalizado.split(" ")
    tokens = [SINONIMOS.get(token, token) for token in tokens if token]
    tokens = [token for token in tokens if token and token not in STOPWORDS]
    return tokens


def reconstruir_indice():
    """Reconstruye el índice completo desde `productos`. Pensado para correr
    después de cada scraping (medido: ~180ms para todo el catálogo)."""

    conexion = conectar()
    try:
        # `productos_fts` es una tabla FTS5 "contentless" (content='') --
        # no soporta DELETE normal (SQLite lo rechaza: "cannot DELETE
        # from contentless fts5 table"). El comando especial 'delete-all'
        # es la forma correcta de vaciarla antes de reinsertar todo.
        conexion.execute("INSERT INTO productos_fts(productos_fts) VALUES ('delete-all')")

        filas = conexion.execute(
            "SELECT id, nombre, categoria, subcategoria FROM productos"
        ).fetchall()

        datos = [
            (
                fila["id"],
                normalizar_texto(fila["nombre"]),
                normalizar_texto(fila["categoria"]),
                normalizar_texto(fila["subcategoria"]),
            )
            for fila in filas
        ]

        conexion.executemany(
            "INSERT INTO productos_fts (rowid, nombre, categoria, subcategoria) VALUES (?, ?, ?, ?)",
            datos,
        )

        conexion.commit()
        return len(datos)
    finally:
        # Investigación "database is locked" en el arranque: si algo entre
        # el INSERT de arriba y el commit lanzaba una excepción (ej. la
        # tabla productos no existía todavía), esta conexión quedaba sin
        # cerrar, con la transacción del 'delete-all' abierta -- un
        # candado de escritura pegado por el resto de la vida del proceso.
        # Cerrar acá, siempre, haya pasado lo que haya pasado, descarta
        # cualquier transacción sin confirmar y libera el candado.
        conexion.close()


def _condicion_fts(token):
    """Condición MATCH para un token: se expande a un OR entre (a) el grupo
    de género curado a mano si el token pertenece a uno, y (b) las variantes
    de plural/singular por regla general de cada una de esas formas."""

    base = VARIANTE_A_GRUPO.get(token, {token})

    variantes = set()
    for variante in base:
        variantes |= _variantes_numero(variante)

    variantes = sorted(variantes)

    if len(variantes) == 1:
        return f'"{variantes[0]}"'

    return "(" + " OR ".join(f'"{variante}"' for variante in variantes) + ")"


def buscar_fts(
    consulta,
    limite=50,
    categorias_permitidas=None,
    exclusiones=None,
    palabras_a_ignorar=None,
):
    """Búsqueda vía FTS5 + bm25 ponderado por columna.

    Los tres últimos parámetros son opcionales y genéricos -- este módulo no
    sabe nada de "conceptos" ni de la capa de intención (ver capa_intencion.py,
    que sí decide cuándo pasarlos). Con todos en None (el valor por defecto),
    el comportamiento es idéntico al de antes de que existiera esa capa:

    - categorias_permitidas: si se da, solo se devuelven productos cuya
      categoria esté en esa lista.
    - exclusiones: si se da, se descartan productos cuyo nombre contenga
      cualquiera de esas palabras.
    - palabras_a_ignorar: tokens de la consulta que no se exigen como parte
      del MATCH (para no forzar un AND que casi nunca se cumple).
    """

    tokens = tokenizar(normalizar_texto(consulta))

    if palabras_a_ignorar:
        tokens = [token for token in tokens if token not in palabras_a_ignorar]

    if not tokens:
        return []

    consulta_fts = " AND ".join(_condicion_fts(token) for token in tokens)

    filtro_extra = ""
    parametros_extra = []

    if categorias_permitidas:
        marcadores = ",".join("?" for _ in categorias_permitidas)
        filtro_extra += f" AND p.categoria IN ({marcadores})"
        parametros_extra.extend(categorias_permitidas)

    if exclusiones:
        for palabra in exclusiones:
            filtro_extra += " AND p.nombre NOT LIKE ?"
            parametros_extra.append(f"%{palabra}%")

    conexion = conectar()

    try:
        filas = conexion.execute(
            f"""
            SELECT
                p.nombre, p.precio, p.categoria, p.proveedor,
                p.id_proveedor, p.url_producto, p.url_imagen,
                p.marca, p.sku, p.subcategoria, p.descripcion,
                p.peso, p.imagenes_adicionales,
                p.familia_id, f.nombre_familia,
                bm25(productos_fts, {PESO_NOMBRE}, {PESO_CATEGORIA}, {PESO_SUBCATEGORIA}) AS puntaje
            FROM productos_fts
            JOIN productos p ON p.id = productos_fts.rowid
            LEFT JOIN familias_producto f ON f.id = p.familia_id
            WHERE productos_fts MATCH ?{filtro_extra}
            ORDER BY puntaje
            LIMIT ?
            """,
            (consulta_fts, *parametros_extra, limite),
        ).fetchall()
    except sqlite3.OperationalError:
        # consulta con sintaxis FTS5 inválida (ej. token con caracteres reservados) -> sin resultados
        filas = []
    finally:
        conexion.close()

    return [dict(fila) for fila in filas]
