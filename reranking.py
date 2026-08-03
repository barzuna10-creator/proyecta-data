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

# Caso real medido (BUSCADOR_AUDITORIA.md): "Quita cementos y limpia juntas"
# le ganaba a cemento real en la búsqueda "cemento" porque PALABRAS_ACCESORIO
# solo cubre patrones de preposición+sustantivo ("para accesorio"), no
# verbos/sustantivos de acción que indican "este producto ACTÚA SOBRE X",
# no "este producto ES X". Confirmados contra el catálogo real (no
# hipotéticos): "quita", "limpia" (adyacentes directos, "Quita cementos"),
# y "limpiador"/"removedor" (con "de" en medio, "Limpiador de vidrios",
# "Removedor de óxido" -- de ahí PREPOSICIONES_SALTABLES abajo).
PALABRAS_ACCION_SOBRE = {
    "quita", "limpia", "limpiador", "limpiadores",
    "removedor", "removedores", "destapador", "destapadores",
    "desmanchador", "desmanchadores", "quitamanchas",
}

# Preposiciones que pueden mediar entre un sustantivo de acción y el
# material sobre el que actúa ("Limpiador DE vidrios", "Removedor DE
# óxido") -- deliberadamente NO se trata "de" como accesorio por sí solo
# (sin un sustantivo de acción antes): "Mortero de cemento" es cemento de
# verdad, no un accesorio de cemento.
PREPOSICIONES_SALTABLES = {"de", "para"}

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


def _frase_como_palabras(frase, palabras_nombre):
    """¿Aparece `frase` como secuencia de PALABRAS COMPLETAS y consecutivas
    dentro de palabras_nombre? A diferencia de un `in` sobre el string
    completo, esto no confunde un plural/derivado con la frase exacta --
    "cemento" no debe leerse como encontrado dentro de "cementos" solo
    porque es un substring literal de esa palabra (caso real medido en
    BUSCADOR_AUDITORIA.md: infla el puntaje de falsos positivos)."""

    palabras_frase = frase.split()
    if not palabras_frase:
        return False

    n = len(palabras_frase)
    return any(
        palabras_nombre[i:i + n] == palabras_frase
        for i in range(len(palabras_nombre) - n + 1)
    )


def _precedida_por_accesorio(palabras_nombre, i):
    """¿La palabra en la posición `i` está precedida por algo que indica
    "esto es un accesorio de" (para, filtro...) o "esto actúa sobre"
    (quita, limpiador...)? Para el segundo caso también revisa dos
    posiciones atrás cuando hay una preposición en medio -- "Limpiador DE
    vidrios" -- sin tratar la preposición como accesorio por sí sola."""

    if i == 0:
        return False
    if palabras_nombre[i - 1] in PALABRAS_ACCESORIO:
        return True
    if palabras_nombre[i - 1] in PALABRAS_ACCION_SOBRE:
        return True
    if (
        palabras_nombre[i - 1] in PREPOSICIONES_SALTABLES
        and i >= 2
        and palabras_nombre[i - 2] in PALABRAS_ACCION_SOBRE
    ):
        return True
    return False


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
        # palabra de "accesorio" o de "acción sobre"? Si al menos una
        # aparición es "limpia" (sin ese prefijo), el token no se penaliza
        # -- ej. "Tornillo 1/4 x 2" tiene una aparición limpia de "tornillo"
        # aunque exista también "Prensa de Tornillo" en el catálogo.
        todas_precedidas = all(
            _precedida_por_accesorio(palabras_nombre, i)
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
        "frase_exacta": 1.0 if frase_consulta and _frase_como_palabras(frase_consulta, palabras_nombre) else 0.0,
        "penalizacion_accesorio": (tokens_accesorio / tokens_en_nombre) if tokens_en_nombre else 0.0,
    }


def _normalizar_bm25(candidatos):
    """bm25 de SQLite es negativo y sin cota (más negativo = mejor), y su
    MAGNITUD no es una señal confiable de qué tan relevante es un candidato
    -- ver BUSCADOR_AUDITORIA.md para los casos reales medidos. Dos
    situaciones concretas producen un bm25 extremo sin que el candidato sea
    más relevante:

    - coincidir solo por una variante rara del token (ej. buscar "cemento"
      y coincidir vía el plural "cementos", que aparece en apenas 1 producto
      de todo el catálogo frente a 34 con "cemento" -- bm25 favorece el
      término más raro, no el más relevante).
    - coincidir a la vez en el nombre y en la categoría del propio producto
      (frecuente en Carbone Store, cuyos nombres de categoría repiten el
      tipo de producto -- "Bloque..." con categoría "Sistema Bloques...").

    La normalización anterior era lineal sobre la magnitud cruda, así que
    un solo candidato con un bm25 extremo por cualquiera de estos motivos
    dominaba el puntaje combinado. Acá se normaliza por RANGO DE POSICIÓN
    dentro del candidate set: el mejor bm25 crudo vale 1.0, el peor vale
    0.0, todo lo demás interpolado por su puesto -- bm25 sigue siendo la
    señal de orden dominante (su ranking relativo se respeta tal cual),
    pero un solo valor atípico ya no puede, solo por su magnitud, opacar
    las demás señales."""

    puntajes = [c["puntaje"] for c in candidatos]
    # Ascendente: bm25 más negativo (mejor) primero.
    unicos = sorted(set(puntajes))
    total = len(unicos)

    if total == 1:
        return [1.0 for _ in puntajes]

    posicion = {valor: indice for indice, valor in enumerate(unicos)}
    return [1.0 - (posicion[p] / (total - 1)) for p in puntajes]


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
