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
sola palabra suelta antes de que este módulo intervenga.

Precisión vs. cobertura (decisión explícita, medida contra los mismos 2
PDFs de referencia en cada paso): se agregaron cinco mejoras --
1) quitar de la consulta la palabra de mecanismo de apertura
   (abatible/corrediza/...), que casi nunca aparece en un nombre real de
   catálogo; 2) relajación progresiva de la consulta (_buscar_con_
   relajacion) con un piso de MINIMO_TOKENS_RELAJACION=2, nunca a una
   sola palabra; 3) reconocimiento de medidas en pulgadas
   (_PATRON_MEDIDA_PULGADAS); 4) veto de categoria_prohibida para piezas
   estructurales; 5) veto de negación (_es_negacion). Cada una se
   implementó, se midió y se revisó manualmente candidato por candidato
   antes de aceptarla -- dos intentos adicionales (relajar sin piso de
   tokens, exigir categoria_esperada como veto duro en vez de bono) se
   revirtieron al confirmar que producían falsos positivos reales o
   rompían un match ya validado.

Resultado final: cobertura 47.9% (34/71), CERO falsos positivos
confirmados tras revisión manual completa de cada match alta/media. Se
priorizó deliberadamente la precisión sobre la cobertura: el objetivo
explícito era superar 70% manteniendo prácticamente cero falsos
positivos, y no se llegó a ese número, pero cada mejora que sí se
aceptó bajó la cobertura de versiones intermedias (que habían llegado a
~62%) porque esas versiones intermedias tenían falsos positivos reales
que se fueron encontrando y eliminando uno por uno -- nunca se relajó
un veto solo para subir el porcentaje.

El límite actual no es un bug pendiente: de los 37 materiales sin match
en la corrida final, la causa dominante es que el producto simplemente
no existe en el catálogo real (vidrios/ventanas de medida no estándar,
acabados especializados como Chukum o EKOWOOD que ninguna ferretería de
este catálogo vende, medidas de madera fuera de rango) -- no un defecto
del algoritmo. Subir la cobertura desde acá requeriría ampliar o
enriquecer el catálogo, no relajar más los vetos de este módulo."""

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

# "8" X 8"" -- formato en pulgadas real del catálogo (vigas H de aluminio),
# con la comilla pegada a CADA número, no solo al segundo -- el patrón de
# arriba no lo reconoce (la comilla rompe el \s* entre número y "x"), así
# que un candidato así queda sin ninguna medida detectada y el veto de
# conflicto no tiene nada que comparar. Verificado contra el catálogo real:
# "Viga Estructural En Forma De H 8\" X 8\". ... Alto 203.2 Mm" -- sin este
# patrón, ninguna de las medidas del nombre se reconoce.
_PATRON_MEDIDA_PULGADAS = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*\"\s*x\s*(\d+(?:[.,]\d+)?)\s*\"", re.IGNORECASE
)
_CM_POR_PULGADA = 2.54


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
    for match in _PATRON_MEDIDA_PULGADAS.finditer(nombre or ""):
        a = _numero(match.group(1))
        b = _numero(match.group(2))
        if a is None or b is None:
            continue
        medidas.append((a * _CM_POR_PULGADA, b * _CM_POR_PULGADA))
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


_PALABRAS_NEGACION = {"sin", "no"}


def _es_negacion(termino_busqueda):
    """Caso real encontrado en el PDF arquitectónico: la fila C1 de un
    cuadro de acabados de cielos dice "Sin cielo" -- significa que ESE
    ambiente NO lleva cielo raso (la terminación real, "Losa expuesta con
    acabado concreto", quedó en otra columna que el parser no usó como
    descripción, ver clasificación de causas). Buscar "cielo" a partir de
    "Sin cielo" encontraba con confianza ALTA un producto real de cielo
    suspendido -- exactamente lo opuesto de lo que dice el plano. Un
    término que EMPIEZA con una negación nunca es un material a buscar,
    sin importar qué palabra siga."""

    tokens = tokenizar(normalizar_texto(termino_busqueda or ""))
    return bool(tokens) and tokens[0] in _PALABRAS_NEGACION


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


def _empieza_por_el_concepto_buscado(termino_busqueda, nombre_candidato, ancla=None):
    """Sin `ancla`: el candidato tiene que empezar por CUALQUIER token
    significativo del término buscado -- correcto cuando el término ES la
    descripción completa del material (acabados, piezas), no hay un
    "objeto" separado del material.

    Con `ancla` (ej. "puerta"/"ventana"): el candidato tiene que empezar
    ESPECÍFICAMENTE por esa palabra, no por cualquier token del término.
    Necesario porque termino_busqueda de puertas/ventanas mezcla el
    OBJETO ("puerta") con su MATERIAL ("lamina de hn") -- sin `ancla`, un
    candidato que es simple lámina plana (ninguna puerta) pasaba el veto
    solo porque "lamina" también es un token válido de la búsqueda. Caso
    real encontrado midiendo la cobertura: "puerta" + "lamina de hn"
    (sin candidatos con esas palabras Y "puerta") relajaba hasta "puerta
    lamina", y sin este ancla, "Lámina melamina MDP..." (que ni siquiera
    es una puerta) pasaba con confianza ALTA."""

    primero = _primer_token_significativo(nombre_candidato)
    if primero is None:
        return False
    if ancla is not None:
        return _comparten_prefijo(primero, ancla)
    tokens_busqueda = extraer_tokens_identidad(termino_busqueda)
    return any(_comparten_prefijo(primero, t) for t in tokens_busqueda)


MINIMO_TOKENS_RELAJACION = 2


def _buscar_con_relajacion(termino_busqueda):
    """buscar_fts() exige AND de todos los tokens -- una frase larga
    (ej. la `descripcion` completa de un acabado: "REPELLO FINO
    ENMASILLADO CON PINTURA COLOR BLANCO") casi nunca calza así de
    literal con un nombre real de catálogo, aunque el material SÍ exista
    ("Repello Fino gris REPEMAX" sí está). Si la consulta completa no
    encuentra nada, se reintenta recortando tokens desde el final --
    nunca desde el principio, porque los nombres reales de este catálogo
    consistentemente empiezan por el concepto (mismo hallazgo que
    _primer_token_significativo) -- hasta que algo aparezca.

    Nunca relaja por debajo de MINIMO_TOKENS_RELAJACION=2 tokens. Caso
    real encontrado midiendo la cobertura: un material con las palabras
    en un orden distinto al esperado (hallazgo aparte, de parser --
    "National Cielo Gypsum MR" en vez de "Cielo Gypsum National", la
    misma lámina de cielo aparece dos veces en el PDF con el orden de
    palabras distinto) relajaba hasta la sola palabra "national", y
    encontraba una BISAGRA de marca "National" -- comparte esa palabra
    por pura coincidencia de marca, no por ser el mismo tipo de producto.
    El veto de "empieza por el concepto" no lo agarra porque "national"
    SÍ es, técnicamente, un token de la búsqueda. Con el piso de 2
    tokens, esta consulta nunca llega a relajarse tan lejos -- se pierde
    algún caso legítimo de una sola palabra (ver
    seleccion_automatica.py), pero ninguna coincidencia de marca
    accidental como esta."""

    candidatos = buscar_fts(termino_busqueda, limite=LIMITE_CANDIDATOS)
    if candidatos:
        return candidatos, False

    tokens = [
        token for token in tokenizar(normalizar_texto(termino_busqueda))
        if token not in TOKENS_GENERICOS and not token.replace(".", "").isdigit()
    ]
    for cantidad in range(len(tokens) - 1, MINIMO_TOKENS_RELAJACION - 1, -1):
        candidatos = buscar_fts(" ".join(tokens[:cantidad]), limite=LIMITE_CANDIDATOS)
        if candidatos:
            return candidatos, True

    return [], True


def seleccionar_producto(
    termino_busqueda,
    medida_esperada_cm=None,
    categoria_esperada=None,
    categoria_prohibida=None,
    ancla=None,
    depurar=False,
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
    similares.py), exigirla a la fuerza descartaría candidatos válidos.

    `categoria_prohibida`, si se da, es un set de palabras (normalizadas)
    de las que basta que UNA aparezca en categoria+subcategoria del
    candidato para vetarlo -- a diferencia de `categoria_esperada` (bono,
    nunca descarta), esto SÍ es un veto duro. Reservado para dominios
    inequívocamente ajenos (ej. "baños" para piezas estructurales de
    madera/acero) -- nunca para exigir la categoría "correcta" (eso ya se
    intentó como veto duro y rompió un match real, "Repello Fino gris",
    porque la taxonomía de categoría varía demasiado entre proveedores
    para exigirla por inclusión; excluir un dominio obviamente incorrecto
    es mucho más seguro que exigir uno "correcto").

    `ancla`, si se da (ej. "puerta"/"ventana"), exige que el candidato
    empiece específicamente por esa palabra -- no por cualquier token de
    `termino_busqueda`. Ver _empieza_por_el_concepto_buscado()."""

    if _es_negacion(termino_busqueda):
        return {"producto": None, "confianza": None, "razones": ["termino_de_negacion"]}

    candidatos, se_relajo_la_consulta = _buscar_con_relajacion(termino_busqueda)
    if not candidatos:
        return {"producto": None, "confianza": None, "razones": ["sin_candidatos_busqueda"]}

    sobrevivientes = []
    for posicion, candidato in enumerate(candidatos):
        razones = []

        if _es_repuesto_o_accesorio(candidato["nombre"]):
            continue  # veto: repuesto/accesorio, nunca el producto en sí

        if not _empieza_por_el_concepto_buscado(termino_busqueda, candidato["nombre"], ancla=ancla):
            continue  # veto: el candidato menciona el concepto pero no ES el concepto

        if categoria_prohibida:
            texto_categoria_veto = normalizar_texto(
                f"{candidato.get('categoria') or ''} {candidato.get('subcategoria') or ''}"
            )
            if any(palabra in texto_categoria_veto for palabra in categoria_prohibida):
                continue  # veto: dominio inequívocamente ajeno (ver categoria_prohibida)

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
        extra = [f"posicion_ranking:{posicion}"]
        if se_relajo_la_consulta:
            extra.append("consulta_relajada")
        resultado["razones"] = razones + extra
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

    La consulta real a buscar_fts() antepone "puerta"/"ventana" -- medido
    contra el catálogo real: sin esto, términos como "corrediza vidrio" no
    traían un solo candidato.

    Usa SOLO el material (`material["material"]`, ej. "madera"), no el
    tipo de apertura (abatible/corrediza/pivotante/ventila/fija) --
    medición real contra el catálogo (ver seleccion_automatica.py,
    hallazgo de cobertura): la palabra del MECANISMO de apertura casi
    nunca aparece en un nombre real de producto, así que exigirla por AND
    o no encuentra nada, o -- cuando sí encuentra algo -- es herraje
    (bisagras, cierres, rodachines) que el veto de "empieza por el
    concepto" ya descarta correctamente. "puerta abatible madera" no
    encontraba nada real; "puerta madera" sí encuentra puertas de madera
    reales. El veto de concepto sigue siendo la única protección real
    contra herraje -- ampliar la consulta no la debilita, solo le da más
    para trabajar.

    Si el material no trae `material` (algún caso raro sin ese dato), cae
    a termino_busqueda completo -- nunca una consulta vacía.

    ancla=palabra_tipo a proposito (hallazgo real midiendo cobertura tras
    relajar la consulta): sin esto, el veto de concepto aceptaba un
    candidato que empezara por CUALQUIER token de la busqueda, incluido
    el material ("lamina") -- una lamina plana pasaba con confianza alta
    solo porque "lamina" tambien era parte de lo buscado, aunque no fuera
    una puerta. Con el ancla, el candidato tiene que empezar
    especificamente por "puerta"/"ventana"."""

    palabra_tipo = "ventana" if es_ventana else "puerta"
    nucleo = material.get("material") or material["termino_busqueda"]
    consulta = f"{palabra_tipo} {nucleo}"
    return seleccionar_producto(
        consulta,
        medida_esperada_cm=medida_puerta_ventana_cm(material),
        categoria_esperada=_CATEGORIA_VENTANA if es_ventana else _CATEGORIA_PUERTA,
        ancla=palabra_tipo,
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


_CATEGORIA_PROHIBIDA_PIEZA = {"banos", "automotriz", "organizacion"}


def seleccionar_para_pieza_estructural(material, depurar=False):
    """material: un dict de analisis["piezas_estructurales"]. Descripciones
    como "columna" o "viga" son genéricas -- también son el nombre de
    accesorios de baño ("Columna de ducha..."), topes para estacionamiento
    ("Protector para columna...") y repuestos de góndola de tienda
    ("Columna de placa final para góndola...") -- verificado contra el
    catálogo real: buscar_fts("columna") no devuelve NI UN SOLO candidato
    de columna estructural, los 8 resultados son de estos tres dominios.
    Ningún otro veto los agarra (todos empiezan literalmente por
    "columna"). categoria_prohibida los descarta -- dominios
    inequívocamente ajenos, no una categoría "correcta" exigida (ver
    seleccionar_producto)."""

    return seleccionar_producto(
        material["termino_busqueda"],
        medida_esperada_cm=medida_pieza_estructural_cm(material),
        categoria_prohibida=_CATEGORIA_PROHIBIDA_PIEZA,
        depurar=depurar,
    )
