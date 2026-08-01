"""Productos similares / sustitutos -- primera versión determinística, sin
IA ni embeddings. No toca FTS5 ni reranking.py: los candidatos salen de
consultas SQL directas sobre `productos`, y el puntaje es aritmética simple
sobre señales explícitas (misma familia, subcategoría, tokens del nombre,
marca, peso, tokens de la descripción).

Antes de diseñar esto se auditaron los datos reales del catálogo:
- familia_id/nombre_familia solo existen para Pinturas (ver familias.py) --
  para el resto del catálogo no hay familia, hay que caer al resto de señales.
- La categoría "General" (mayoría Carbone Store) mezcla productos sin
  relación real entre sí -- "misma categoría" ahí no alcanza como filtro,
  se exige más evidencia (ver UMBRAL_TOKENS_CATEGORIA_GENERAL).
- subcategoria tiene mayúsculas/minúsculas inconsistentes entre proveedores
  ("Herramientas Manuales" vs "Herramientas manuales") -- se compara
  normalizada, nunca literal.
- marca solo existe en Carbone Store y El Lagar; peso solo en EPA (unidad
  sin confirmar). Son señales de bonus, nunca requisitos.
"""

from db import conectar
from busqueda import normalizar_texto, tokenizar

LIMITE_DEFECTO = 6

# Pesos de cada señal -- mismo estilo que reranking.py: constantes nombradas,
# no números mágicos sueltos en el código.
PUNTAJE_MISMA_FAMILIA = 100
PUNTAJE_MISMA_SUBCATEGORIA = 6
PUNTAJE_POR_TOKEN_NOMBRE = 2
TOPE_PUNTAJE_TOKENS_NOMBRE = 8
PUNTAJE_MISMA_MARCA = 5
PUNTAJE_PESO_SIMILAR = 3
PUNTAJE_POR_TOKEN_DESCRIPCION = 1
TOPE_PUNTAJE_TOKENS_DESCRIPCION = 4

# Umbral mínimo de puntaje para que un candidato se muestre -- por debajo de
# esto no se recomienda nada antes que mostrar una relación débil/inventada.
UMBRAL_MINIMO = 8

# Tolerancia relativa para considerar dos pesos "similares" (20%).
TOLERANCIA_PESO = 0.20

# "General" (mayoría Carbone Store) es una categoría-bolsa sin relación real
# entre sus miembros -- ahí se exige más de 1 token compartido para calificar.
CATEGORIA_SIN_SENAL = "General"
MINIMO_TOKENS_CATEGORIA_SIN_SENAL = 2

CAMPOS_PRODUCTO = (
    "p.id, p.proveedor, p.id_proveedor, p.sku, p.nombre, p.marca, p.categoria, "
    "p.subcategoria, p.precio, p.descripcion, p.url_imagen, p.url_producto, "
    "p.peso, p.imagenes_adicionales, p.familia_id, f.nombre_familia"
)
DESDE_PRODUCTO = "FROM productos p LEFT JOIN familias_producto f ON f.id = p.familia_id"

# tokenizar() (busqueda.py) ya quita STOPWORDS, pero deja pasar unidades y
# números sueltos ("w", "pulg", "1", "2") -- necesarios para que el
# buscador encuentre "taladro 1/2 pulgada", pero *no* deben poder calificar
# por sí solos a un candidato como "similar" (un esmeril y un taladro
# comparten "1", "2", "pulg" y "w" sin ser el mismo tipo de producto en
# absoluto -- se confirmó este caso real probando el algoritmo). Se
# excluyen de la comparación de similitud, no de la búsqueda.
TOKENS_GENERICOS = {
    "w", "kw", "v", "hp", "amp", "a", "hz",
    "pulg", "mm", "cm", "m", "kg", "g", "lb", "lbs", "oz", "ml", "lt", "l", "gal",
    "x",
}


def _tokens_nombre(nombre):
    tokens = tokenizar(normalizar_texto(nombre or ""))
    return {t for t in tokens if t not in TOKENS_GENERICOS and not t.replace(".", "").isdigit()}


def _tokens_descripcion(descripcion, maximo=40):
    """Bolsa de palabras simple de la descripción -- se limita a las
    primeras `maximo` palabras significativas para no dejar que una
    descripción muy larga domine el puntaje frente a una corta."""

    tokens = tokenizar(normalizar_texto(descripcion or ""))
    return set(tokens[:maximo])


def _peso_numerico(peso_texto):
    if not peso_texto:
        return None
    try:
        return float(str(peso_texto).replace(",", "."))
    except ValueError:
        return None


def _puntuar(objetivo, candidato, tokens_objetivo, tokens_desc_objetivo):
    """Devuelve (puntaje, razones) para un candidato frente al producto
    objetivo. Nunca lanza -- candidatos con datos incompletos simplemente no
    suman esa señal."""

    puntaje = 0
    razones = []

    misma_familia = (
        objetivo["familia_id"] is not None
        and candidato["familia_id"] == objetivo["familia_id"]
    )
    if misma_familia:
        puntaje += PUNTAJE_MISMA_FAMILIA
        razones.append("misma_familia")

    subcat_obj = normalizar_texto(objetivo["subcategoria"] or "")
    subcat_cand = normalizar_texto(candidato["subcategoria"] or "")
    misma_subcategoria = bool(subcat_obj) and subcat_obj == subcat_cand
    if misma_subcategoria:
        puntaje += PUNTAJE_MISMA_SUBCATEGORIA
        razones.append("misma_subcategoria")

    tokens_candidato = _tokens_nombre(candidato["nombre"])
    tokens_compartidos = tokens_objetivo & tokens_candidato
    if tokens_compartidos:
        puntos_tokens = min(
            len(tokens_compartidos) * PUNTAJE_POR_TOKEN_NOMBRE,
            TOPE_PUNTAJE_TOKENS_NOMBRE,
        )
        puntaje += puntos_tokens
        razones.append(f"tokens_nombre:{','.join(sorted(tokens_compartidos))}")

    marca_obj = (objetivo["marca"] or "").strip().lower()
    marca_cand = (candidato["marca"] or "").strip().lower()
    misma_marca = bool(marca_obj) and marca_obj == marca_cand
    if misma_marca:
        puntaje += PUNTAJE_MISMA_MARCA
        razones.append("misma_marca")

    peso_obj = _peso_numerico(objetivo["peso"])
    peso_cand = _peso_numerico(candidato["peso"])
    if peso_obj is not None and peso_cand is not None and peso_obj > 0:
        diferencia_relativa = abs(peso_obj - peso_cand) / peso_obj
        if diferencia_relativa <= TOLERANCIA_PESO:
            puntaje += PUNTAJE_PESO_SIMILAR
            razones.append("peso_similar")

    tokens_desc_candidato = _tokens_descripcion(candidato["descripcion"])
    tokens_desc_compartidos = tokens_desc_objetivo & tokens_desc_candidato
    if tokens_desc_compartidos:
        puntos_desc = min(
            len(tokens_desc_compartidos) * PUNTAJE_POR_TOKEN_DESCRIPCION,
            TOPE_PUNTAJE_TOKENS_DESCRIPCION,
        )
        puntaje += puntos_desc
        razones.append(f"tokens_descripcion:{len(tokens_desc_compartidos)}")

    # Filtro duro anti-incompatibles: compartir solo la categoría (que ya es
    # requisito para ser candidato, ver obtener_similares) nunca alcanza --
    # hace falta subcategoría igual o al menos 1 token real del nombre.
    evidencia_minima = misma_subcategoria or bool(tokens_compartidos)
    if not misma_familia and not evidencia_minima:
        return 0, []

    # "General" no discrimina nada por sí sola -- exige más tokens.
    if (
        not misma_familia
        and objetivo["categoria"] == CATEGORIA_SIN_SENAL
        and len(tokens_compartidos) < MINIMO_TOKENS_CATEGORIA_SIN_SENAL
    ):
        return 0, []

    return puntaje, razones


def obtener_similares(proveedor, id_proveedor, limite=LIMITE_DEFECTO, depurar=False):
    """Hasta `limite` productos similares/sustitutos para el producto
    (proveedor, id_proveedor). Lista vacía si no hay candidatos confiables
    -- nunca rellena con coincidencias débiles."""

    conexion = conectar()

    objetivo = conexion.execute(
        f"SELECT {CAMPOS_PRODUCTO} {DESDE_PRODUCTO} WHERE p.proveedor = ? AND p.id_proveedor = ?",
        (proveedor, id_proveedor),
    ).fetchone()

    if objetivo is None:
        conexion.close()
        return []

    if objetivo["familia_id"] is not None:
        candidatos = conexion.execute(
            f"SELECT {CAMPOS_PRODUCTO} {DESDE_PRODUCTO} "
            "WHERE p.familia_id = ? AND NOT (p.proveedor = ? AND p.id_proveedor = ?)",
            (objetivo["familia_id"], proveedor, id_proveedor),
        ).fetchall()
    else:
        candidatos = []

    ids_ya_vistos = {c["id"] for c in candidatos}

    candidatos_categoria = conexion.execute(
        f"SELECT {CAMPOS_PRODUCTO} {DESDE_PRODUCTO} "
        "WHERE p.categoria = ? AND NOT (p.proveedor = ? AND p.id_proveedor = ?)",
        (objetivo["categoria"], proveedor, id_proveedor),
    ).fetchall()

    conexion.close()

    candidatos = list(candidatos) + [
        c for c in candidatos_categoria if c["id"] not in ids_ya_vistos
    ]

    tokens_objetivo = _tokens_nombre(objetivo["nombre"])
    tokens_desc_objetivo = _tokens_descripcion(objetivo["descripcion"])

    puntuados = []
    for candidato in candidatos:
        puntaje, razones = _puntuar(objetivo, candidato, tokens_objetivo, tokens_desc_objetivo)
        if puntaje >= UMBRAL_MINIMO:
            puntuados.append((puntaje, candidato, razones))

    puntuados.sort(key=lambda item: item[0], reverse=True)
    seleccionados = puntuados[:limite]

    resultado = []
    for puntaje, candidato, razones in seleccionados:
        item = dict(candidato)
        if depurar:
            item["_puntaje"] = puntaje
            item["_razones"] = razones
        resultado.append(item)

    return resultado
