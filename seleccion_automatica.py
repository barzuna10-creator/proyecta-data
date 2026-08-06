"""Selección automática de un producto real del catálogo para un material
detectado en un plano (puerta, ventana, acabado o pieza estructural) --
determinista, sin IA ni embeddings, igual que similares.py y
equivalencias.py. No toca ninguno de los dos: reutiliza lo que ya está
calibrado (busqueda.buscar_fts para candidatos, equivalencias.PALABRAS_PARTE
para el veto de repuestos/accesorios) y agrega, acá, lo único que no
existía todavía: corroborar la MEDIDA del material contra el nombre del
candidato.

Principio de diseño (pedido explícito: "no quiero coincidencias simples
solo por palabras si pueden generar materiales incorrectos"): un candidato
nunca se selecciona solo porque el texto calza -- tiene que sobrevivir tres
vetos duros antes de que cualquier puntaje de texto cuente:

1. No ser un repuesto/accesorio (equivalencias.PALABRAS_PARTE) -- un
   "repuesto de bisagra para puerta X" nunca es sustituto de la puerta.
2. Empezar por el mismo concepto que se busca, no solo mencionarlo. Caso
   real encontrado probando este módulo contra el catálogo real: buscar
   "Enchape de Porcelanato" devolvía primero "Sistema para manipular
   porcelanatos de formato grande" -- una herramienta de instalación, no
   el porcelanato en sí. FTS5 indexa categoría además del nombre (ver
   busqueda.py), así que "enchape" venía de `categoria="Pisos y
   Enchapes"`, no del propio nombre del candidato -- el AND de tokens
   por sí solo no alcanza para evitar esto. La corrección: el PRIMER
   token significativo del nombre del candidato tiene que compartir
   prefijo con algún token significativo del término buscado (ver
   _primer_token_significativo/_comparten_prefijo) -- los nombres reales
   de este catálogo consistentemente empiezan por el tipo de producto
   ("Porcelanato...", "Puerta...", "Cemento..."), nunca por cómo se usa o
   instala.
3. Si el material trae una medida (ancho×alto para puertas/ventanas,
   ancho_mm×alto_mm para piezas) Y el candidato también tiene una medida
   explícita en su nombre, ambas tienen que coincidir dentro de tolerancia.
   Un candidato con una medida EXPLÍCITAMENTE distinta se descarta, nunca
   se degrada a confianza baja -- es un descalificador duro, mismo
   principio que especificaciones.py usa para specs de compatibilidad
   física.

Sin ninguna medida en ninguno de los dos lados (el caso más común para
puertas/ventanas reales, cuyos SKU de catálogo rara vez repiten la medida
exacta del plano), no hay corroboración posible -- el candidato puede
seguir siendo válido (ya sobrevivió los tres vetos), pero nunca llega a
confianza "alta" solo por texto.

busqueda.buscar_fts() ya arma el término de búsqueda con AND entre tokens
(ver busqueda.py) -- eso ya descarta la mayoría de coincidencias de una
sola palabra suelta antes de que este módulo intervenga."""

import re

from busqueda import buscar_fts, normalizar_texto, tokenizar
from equivalencias import PALABRAS_PARTE, extraer_tokens_identidad
from similares import TOKENS_GENERICOS

CONFIANZA_ALTA = "alta"
CONFIANZA_MEDIA = "media"
CONFIANZA_BAJA = "baja"

LIMITE_CANDIDATOS = 8

# Tolerancia relativa para considerar que dos medidas "coinciden" -- mismo
# criterio (20%) que ya usan similares.py (peso) y especificaciones.py
# (rendimiento) para este tipo de comparación numérica.
TOLERANCIA_MEDIDA = 0.20

# "35 x 200 cm", "0.95x2.40m", "90 x 245 mm" -- ancho x alto, con la unidad
# opcional pegada al segundo número (o ausente, se asume cm si los valores
# son de doble o triple dígito, m si son de un dígito con decimales -- ver
# _normalizar_a_cm). No exige la unidad en el patrón porque muchos nombres
# reales de catálogo la omiten ("Puerta 60 x 200").
_PATRON_MEDIDA_NOMBRE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*x\s*(\d+(?:[.,]\d+)?)\s*(cm|mm|m)?", re.IGNORECASE
)


def _numero(texto):
    try:
        return float(texto.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def _normalizar_a_cm(valor, unidad):
    if unidad and unidad.lower() == "mm":
        return valor / 10
    if unidad and unidad.lower() == "m":
        return valor * 100
    # Sin unidad explícita: valores chicos con decimales son casi siempre
    # metros en este catálogo (puertas/ventanas: "0.95 x 2.40"); valores
    # grandes sin decimales son casi siempre cm ("60 x 200"). Calibrado
    # contra los nombres reales vistos en similares.py/especificaciones.py.
    if valor < 10:
        return valor * 100
    return valor


def _medidas_en_nombre(nombre):
    """Todas las medidas ancho×alto (en cm) que aparecen en un nombre --
    puede haber más de una mención, se comparan todas contra la medida
    esperada y basta con que UNA coincida."""

    medidas = []
    for match in _PATRON_MEDIDA_NOMBRE.finditer(nombre or ""):
        a = _numero(match.group(1))
        b = _numero(match.group(2))
        unidad = match.group(3)
        if a is None or b is None:
            continue
        medidas.append((_normalizar_a_cm(a, unidad), _normalizar_a_cm(b, unidad)))
    return medidas


def _coincide_alguna_medida(medida_esperada_cm, medidas_candidato_cm):
    ancho_esp, alto_esp = medida_esperada_cm
    for ancho_cand, alto_cand in medidas_candidato_cm:
        # Ancho/alto pueden venir invertidos entre el plano y el catálogo
        # (una puerta "0.95 x 2.40" y un SKU listado "2.40 x 0.95" son la
        # misma medida) -- se compara en ambos órdenes.
        for (a1, b1), (a2, b2) in [
            ((ancho_esp, alto_esp), (ancho_cand, alto_cand)),
            ((ancho_esp, alto_esp), (alto_cand, ancho_cand)),
        ]:
            if a1 <= 0 or b1 <= 0:
                continue
            if abs(a1 - a2) / a1 <= TOLERANCIA_MEDIDA and abs(b1 - b2) / b1 <= TOLERANCIA_MEDIDA:
                return True
    return False


def _conflicto_de_medida(medida_esperada_cm, medidas_candidato_cm):
    """True si el candidato SÍ trae una medida explícita en el nombre y
    NINGUNA calza con la esperada -- descalificador duro. False si no hay
    medida en el candidato (no se puede confirmar ni descartar) o si
    alguna sí coincide."""

    if not medidas_candidato_cm:
        return False
    return not _coincide_alguna_medida(medida_esperada_cm, medidas_candidato_cm)


def _es_repuesto_o_accesorio(nombre):
    return bool(extraer_tokens_identidad(nombre) & PALABRAS_PARTE)


def _tiene_precio(candidato):
    return candidato.get("precio") is not None and candidato["precio"] > 0


_LARGO_MINIMO_PREFIJO = 5


def _comparten_prefijo(a, b):
    """Coincidencia de prefijo, no igualdad exacta -- 'porcelanato' y
    'porcelanatos' (singular/plural) tienen que reconocerse como el mismo
    concepto sin necesidad de un stemmer nuevo. Para palabras cortas
    (menos de _LARGO_MINIMO_PREFIJO caracteres) exige igualdad exacta --
    un prefijo de 5 sobre una palabra de 3 letras no distingue nada."""

    corto = min(len(a), len(b))
    if corto < _LARGO_MINIMO_PREFIJO:
        return a == b
    return a[:_LARGO_MINIMO_PREFIJO] == b[:_LARGO_MINIMO_PREFIJO]


def _primer_token_significativo(nombre):
    """A diferencia de extraer_tokens_identidad() (un set, sin orden), acá
    el ORDEN importa -- ver el veto de "empieza por el mismo concepto" en
    el docstring del módulo."""

    for token in tokenizar(normalizar_texto(nombre or "")):
        if token in TOKENS_GENERICOS or token.replace(".", "").isdigit():
            continue
        return token
    return None


def _empieza_por_el_concepto_buscado(termino_busqueda, nombre_candidato):
    primero = _primer_token_significativo(nombre_candidato)
    if primero is None:
        return False
    tokens_busqueda = extraer_tokens_identidad(termino_busqueda)
    return any(_comparten_prefijo(primero, t) for t in tokens_busqueda)


def seleccionar_producto(
    termino_busqueda, medida_esperada_cm=None, categoria_esperada=None, depurar=False
):
    """Busca en el catálogo real un producto para `termino_busqueda`
    (típicamente el `termino_busqueda` que ya trae cada material del
    análisis de un plano) y devuelve:

        {"producto": dict | None, "confianza": "alta"|"media"|"baja"|None,
         "razones": [...] (solo si depurar=True)}

    `producto` y `confianza` son None si ningún candidato sobrevivió los
    vetos -- nunca se fuerza una selección de baja calidad.

    `medida_esperada_cm`, si se da, es una tupla (ancho_cm, alto_cm) contra
    la que se corrobora (o descarta) cada candidato que traiga su propia
    medida en el nombre.

    `categoria_esperada`, si se da, es un set de palabras (normalizadas)
    de las que basta que UNA aparezca en categoria+subcategoria del
    candidato para sumar como corroboración -- nunca un veto: la taxonomía
    de categoría varía entre los 6 proveedores de este catálogo (ver
    similares.py), exigirla a la fuerza descartaría candidatos válidos."""

    candidatos = buscar_fts(termino_busqueda, limite=LIMITE_CANDIDATOS)
    if not candidatos:
        return {"producto": None, "confianza": None, "razones": ["sin_candidatos_busqueda"]}

    sobrevivientes = []
    for posicion, candidato in enumerate(candidatos):
        razones = []

        if _es_repuesto_o_accesorio(candidato["nombre"]):
            continue  # veto: repuesto/accesorio, nunca el producto en sí

        if not _empieza_por_el_concepto_buscado(termino_busqueda, candidato["nombre"]):
            continue  # veto: el candidato menciona el concepto pero no ES el concepto

        medida_confirmada = False
        if medida_esperada_cm is not None:
            medidas_candidato = _medidas_en_nombre(candidato["nombre"])
            if _conflicto_de_medida(medida_esperada_cm, medidas_candidato):
                continue  # veto: el candidato trae una medida distinta
            if medidas_candidato and _coincide_alguna_medida(medida_esperada_cm, medidas_candidato):
                medida_confirmada = True
                razones.append("medida_confirmada")

        categoria_confirmada = False
        if categoria_esperada:
            texto_categoria = normalizar_texto(
                f"{candidato.get('categoria') or ''} {candidato.get('subcategoria') or ''}"
            )
            if any(palabra in texto_categoria for palabra in categoria_esperada):
                categoria_confirmada = True
                razones.append("categoria_confirmada")

        if not _tiene_precio(candidato):
            razones.append("sin_precio")

        sobrevivientes.append((posicion, candidato, medida_confirmada, categoria_confirmada, razones))

    if not sobrevivientes:
        return {"producto": None, "confianza": None, "razones": ["todos_los_candidatos_vetados"]}

    # buscar_fts ya devuelve ordenado por relevancia (bm25) -- el primer
    # sobreviviente en ese orden es el elegido; la corroboración de medida
    # /categoría decide la CONFIANZA, no el orden (forzar arriba un
    # candidato con medida confirmada pero peor texto sería exactamente el
    # tipo de sustitución arriesgada que este módulo evita).
    posicion, elegido, medida_confirmada, categoria_confirmada, razones = sobrevivientes[0]

    if medida_confirmada or (posicion == 0 and categoria_confirmada and _tiene_precio(elegido)):
        confianza = CONFIANZA_ALTA
    elif posicion == 0 and _tiene_precio(elegido):
        # Primer resultado de la búsqueda (todos sus tokens hicieron
        # match, ver busqueda.py), con precio real, pero sin ninguna
        # medida ni categoría que lo confirme -- confianza media, no
        # alta: es el caso típico y esperado de puertas/ventanas reales.
        confianza = CONFIANZA_MEDIA
    else:
        confianza = CONFIANZA_BAJA

    resultado = {"producto": elegido, "confianza": confianza}
    if depurar:
        resultado["razones"] = razones + [f"posicion_ranking:{posicion}"]
    return resultado


def medida_puerta_ventana_cm(material):
    """(ancho_cm, alto_cm) para un material de tipo puerta/ventana (ver
    api/adaptador_planos.py) -- None si no hay medida confiable. ancho/alto
    ya vienen en metros (lectura_planos/cuadros.py)."""

    ancho, alto = material.get("ancho"), material.get("alto")
    if ancho is None or alto is None:
        return None
    return (ancho * 100, alto * 100)


def medida_pieza_estructural_cm(material):
    """(ancho_cm, alto_cm) para una pieza estructural -- el largo no se usa
    (rara vez se refleja en el nombre de un producto de catálogo, a
    diferencia de la sección transversal ancho×alto, que sí es como se
    describe la madera/perfil comercialmente)."""

    ancho_mm, alto_mm = material.get("ancho_mm"), material.get("alto_mm")
    if ancho_mm is None or alto_mm is None:
        return None
    return (ancho_mm / 10, alto_mm / 10)


# Palabras de categoría/subcategoría esperadas por tipo de material -- solo
# suman como corroboración (nunca descartan, ver seleccionar_producto), así
# que no hace falta que cubran cada proveedor con precisión perfecta.
_CATEGORIA_PUERTA = {"puerta"}
_CATEGORIA_VENTANA = {"ventana"}
_CATEGORIA_ACABADO_POR_UBICACION = {
    "pisos": {"piso", "ceramica", "porcelanato", "azulejo"},
    "muros_y_paredes": {"pared", "muro", "azulejo", "ceramica", "enchape"},
    "cielos": {"cielo", "gypsum", "durock"},
}


def seleccionar_para_puerta_ventana(material, es_ventana, depurar=False):
    """material: un dict de analisis["puertas"] o analisis["ventanas"]
    (ver api/adaptador_planos.py) -- misma forma para ambas, así que hay
    que decir explícitamente cuál es (`es_ventana`): el propio dict del
    material NUNCA trae la palabra "puerta"/"ventana" -- termino_busqueda
    es solo tipo+material ("corrediza vidrio"), nunca el tipo de vano.

    Por el mismo motivo, la consulta real a buscar_fts() antepone
    "puerta"/"ventana" a termino_busqueda -- medido contra el catálogo
    real: sin esto, términos como "corrediza vidrio" no traían un solo
    candidato (ver seleccion_automatica.py, hallazgo de cobertura). No
    modifica termino_busqueda en sí (eso seguiría rompiendo la búsqueda
    editable manual que ya usa MaterialesDelPlano/FilaMaterialEditable) --
    solo la consulta que arma este módulo, puertas adentro."""

    palabra_tipo = "ventana" if es_ventana else "puerta"
    consulta = f"{palabra_tipo} {material['termino_busqueda']}"
    return seleccionar_producto(
        consulta,
        medida_esperada_cm=medida_puerta_ventana_cm(material),
        categoria_esperada=_CATEGORIA_VENTANA if es_ventana else _CATEGORIA_PUERTA,
        depurar=depurar,
    )


def seleccionar_para_acabado(material, depurar=False):
    """material: un dict de analisis["acabados"]. Sin corroboración de
    medida -- un acabado (pintura, cerámica, gypsum) no se describe por
    ancho×alto como una puerta."""

    return seleccionar_producto(
        material["termino_busqueda"],
        categoria_esperada=_CATEGORIA_ACABADO_POR_UBICACION.get(material.get("ubicacion")),
        depurar=depurar,
    )


def seleccionar_para_pieza_estructural(material, depurar=False):
    """material: un dict de analisis["piezas_estructurales"]."""

    return seleccionar_producto(
        material["termino_busqueda"],
        medida_esperada_cm=medida_pieza_estructural_cm(material),
        depurar=depurar,
    )
