"""Etapa 3: re-ranking en Python sobre el candidate set de FTS5.

FTS5 + bm25 (busqueda.py) resuelve la recuperación: encontrar candidatos
razonables de forma barata sobre 30,000+ productos. Este módulo resuelve el
orden -- qué tan bien representa cada candidato la intención principal de la
búsqueda, no solo si contiene las palabras en algún lado del nombre.

Señales explícitas y auditables, sin IA ni modelo entrenado:
- posición del término en el nombre (más al inicio = más probable que sea
  el sustantivo principal, no un accesorio mencionado de pasada)
- frase exacta de la consulta dentro del nombre
- cobertura: qué fracción de los tokens de la consulta aparecen literalmente
  en el nombre (no solo en categoría/subcategoría)
- coincidencia de los tokens de la consulta con la categoría del producto
- penalización cuando un token de la consulta SOLO aparece precedido por
  palabras que típicamente introducen un accesorio/repuesto de otra cosa
  ("para", "accesorio", "repuesto", "soporte", "filtro")
- reducción de variantes con nombre casi idéntico que monopolizan el tope
  del ranking -- se reordenan, nunca se descartan ni se fusionan.

Se aplica sobre un candidate set amplio (ver busqueda.buscar_fts con un
límite alto) y devuelve la lista reordenada, ya recortada al límite final.
Deliberadamente NO aplica cuotas por proveedor.
"""

from busqueda import normalizar_texto, tokenizar, VARIANTE_A_GRUPO, _variantes_numero

PALABRAS_ACCESORIO = {
    "para", "accesorio", "accesorios", "repuesto", "repuestos",
    "soporte", "soportes", "filtro", "filtros", "adaptador", "adaptadores",
}

# bm25 sigue siendo la señal dominante (ya demostró funcionar bien para
# recuperación) -- las señales de Python ajustan el orden dentro del
# candidate set que ya trajo FTS5, no lo reemplazan.
PESO_BM25 = 1.0
PESO_POSICION = 0.6
PESO_FRASE_EXACTA = 0.5
PESO_COBERTURA = 0.8
PESO_CATEGORIA = 0.2
PESO_PENALIZACION_ACCESORIO = 1.2

MAX_POR_FIRMA_EN_TOP = 3


def _tokens_expandidos(token):
    """Todas las formas equivalentes de un token (género + plural/singular) --
    la misma expansión que ya usa la consulta FTS5 (ver busqueda._condicion_fts)
    -- para poder reconocerlas dentro del nombre al calcular las señales."""

    base = VARIANTE_A_GRUPO.get(token, {token})
    variantes = set()
    for variante in base:
        variantes |= _variantes_numero(variante)
    return variantes


def _posiciones_en_nombre(token, palabras_nombre):
    variantes = _tokens_expandidos(token)
    return [i for i, palabra in enumerate(palabras_nombre) if palabra in variantes]


def _calcular_senales(candidato, tokens_consulta, frase_consulta):
    nombre_normalizado = normalizar_texto(candidato["nombre"])
    palabras_nombre = nombre_normalizado.split()
    palabras_categoria = normalizar_texto(candidato.get("categoria") or "").split()

    tokens_en_nombre = 0
    posicion_minima = None
    tokens_accesorio = 0

    for token in tokens_consulta:
        posiciones = _posiciones_en_nombre(token, palabras_nombre)

        if not posiciones:
            continue

        tokens_en_nombre += 1

        if posicion_minima is None or min(posiciones) < posicion_minima:
            posicion_minima = min(posiciones)

        # ¿TODAS las apariciones de este token están precedidas por una
        # palabra de "accesorio"? Si al menos una aparición es "limpia" (sin
        # ese prefijo), el token no se penaliza -- ej. "Tornillo 1/4 x 2"
        # tiene una aparición limpia de "tornillo" aunque exista también
        # "Prensa de Tornillo" en el catálogo.
        todas_precedidas = all(
            i > 0 and palabras_nombre[i - 1] in PALABRAS_ACCESORIO
            for i in posiciones
        )
        if todas_precedidas:
            tokens_accesorio += 1

    total_tokens = len(tokens_consulta) or 1

    return {
        "bonus_posicion": 1.0 / (1 + posicion_minima) if posicion_minima is not None else 0.0,
        "cobertura": tokens_en_nombre / total_tokens,
        "bonus_categoria": sum(
            1 for t in tokens_consulta if _tokens_expandidos(t) & set(palabras_categoria)
        ) / total_tokens,
        "frase_exacta": 1.0 if frase_consulta and frase_consulta in nombre_normalizado else 0.0,
        "penalizacion_accesorio": (tokens_accesorio / tokens_en_nombre) if tokens_en_nombre else 0.0,
    }


def _normalizar_bm25(candidatos):
    """bm25 de SQLite es negativo y sin cota (más negativo = mejor). Se
    normaliza linealmente al rango [0, 1] dentro del propio candidate set de
    esta consulta, para poder combinarlo con las demás señales (que ya están
    en [0, 1])."""

    puntajes = [c["puntaje"] for c in candidatos]
    peor, mejor = max(puntajes), min(puntajes)
    rango = (peor - mejor) or 1.0
    return [(peor - p) / rango for p in puntajes]


def _firma_dedup(nombre):
    """Firma deliberadamente simple para detectar variantes con nombre casi
    idéntico. A diferencia de familias.py, NO intenta identificar "el mismo
    producto" con precisión (ahí el error tiene costo: fusiona tarjetas) --
    aquí el error es barato (como mucho reordena de más), así que basta con
    quitar los números y comparar las primeras palabras."""

    palabras = [p for p in normalizar_texto(nombre).split() if not p.replace(".", "").isdigit()]
    return " ".join(palabras[:4])


def reordenar(candidatos, consulta, limite=50, depurar=False):
    """Reordena `candidatos` (ya filtrados por FTS5 MATCH, con su `puntaje`
    bm25) combinando bm25 con las señales de arriba, y recorta a `limite`.
    Con depurar=True conserva las señales calculadas en cada resultado, útil
    para auditar por qué quedó en esa posición."""

    if not candidatos:
        return []

    tokens_consulta = tokenizar(normalizar_texto(consulta))
    frase_consulta = normalizar_texto(consulta)
    bm25_normalizado = _normalizar_bm25(candidatos)

    puntuados = []
    for candidato, bm25_norm in zip(candidatos, bm25_normalizado):
        senales = _calcular_senales(candidato, tokens_consulta, frase_consulta)

        puntaje = (
            PESO_BM25 * bm25_norm
            + PESO_POSICION * senales["bonus_posicion"]
            + PESO_FRASE_EXACTA * senales["frase_exacta"]
            + PESO_COBERTURA * senales["cobertura"]
            + PESO_CATEGORIA * senales["bonus_categoria"]
            - PESO_PENALIZACION_ACCESORIO * senales["penalizacion_accesorio"]
        )

        item = dict(candidato)
        item["_puntaje_rerank"] = puntaje
        if depurar:
            item["_senales"] = senales
        puntuados.append(item)

    puntuados.sort(key=lambda c: -c["_puntaje_rerank"])

    # Reducir variantes casi idénticas que monopolizan el tope: no se
    # descartan, se difieren para que no ocupen más de MAX_POR_FIRMA_EN_TOP
    # puestos entre los primeros resultados devueltos.
    contador_firma = {}
    principales, diferidos = [], []
    for candidato in puntuados:
        firma = _firma_dedup(candidato["nombre"])
        if contador_firma.get(firma, 0) < MAX_POR_FIRMA_EN_TOP:
            contador_firma[firma] = contador_firma.get(firma, 0) + 1
            principales.append(candidato)
        else:
            diferidos.append(candidato)

    resultado = (principales + diferidos)[:limite]

    if not depurar:
        for c in resultado:
            c.pop("_puntaje_rerank", None)

    return resultado
