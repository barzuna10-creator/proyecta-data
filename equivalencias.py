"""Motor de equivalencias entre proveedores -- determinista, sin IA ni
embeddings. Responde una pregunta más estricta que similares.py o
especificaciones.py por separado: "¿son estos dos productos, de
proveedores DISTINTOS, literalmente el mismo producto comercial?" (no
"parecido", no "sustituto razonable" -- el mismo artículo).

No inventa una heurística nueva de comparación de specs: reutiliza
especificaciones.py (medidas, calibre, potencia, unidad de venta,
volumen -- ya validado contra nombres reales) tal cual, y busqueda.py
(normalizar_texto/tokenizar) para los tokens. Lo único genuinamente
nuevo acá es:

1. Extraer un "código" (modelo/SKU de fabricante) embebido en el nombre.
2. Comparar marca.
3. Combinar todo -- specs + código + marca + tokens -- en un veredicto
   determinista de tres niveles, y un índice precomputado (ver
   calcular_equivalencias()) que agrupa productos de distintos
   proveedores en el mismo "grupo de equivalencia", reutilizable por
   cualquier consumidor (comparador, similares, presupuestos,
   cotizaciones, búsqueda) sin que cada uno tenga que re-derivar esto
   por su cuenta.

Calibrado contra el catálogo real de los 6 proveedores (60,421
productos) antes de fijar las reglas -- ver EQUIVALENCIAS.md para la
medición completa de calidad (precisión, cobertura, falsos positivos y
negativos con ejemplos reales). Ningún patrón de este archivo es
específico de un proveedor: el filtrado de "código genérico" (SCH40,
GU10, RJ45, IP65 -- estándares de la industria, no SKUs de un producto
puntual) se calcula por frecuencia real en el catálogo, no por una
lista fija -- así escala solo a un séptimo proveedor sin tocar código.
"""

import re
from collections import defaultdict

from busqueda import normalizar_texto, tokenizar
from especificaciones import (
    SPECS_COMPATIBILIDAD,
    SPECS_RENDIMIENTO,
    SPECS_UNIDAD_VENTA,
    comparar_specs,
    extraer_presentacion_pintura,
    extraer_specs,
)
from similares import TOKENS_GENERICOS

EQUIVALENCIA_CONFIRMADA = "equivalencia_confirmada"
EQUIVALENCIA_PROBABLE = "equivalencia_probable"
NO_EQUIVALENTE = "no_comparable"

# Un código real de fabricante (DW5402, BP1803BL, STHT70150...) casi
# siempre empieza con letras (prefijo de marca/serie) y contiene al menos
# un dígito -- a diferencia de un valor de especificación con su unidad
# pegada (180W, 3000K, 9PCS, 35CM), que empieza con el número. Calibrado
# contra el catálogo real: esta única regla estructural, sin lista de
# unidades a excluir, ya separa limpiamente ambos casos (ver
# EQUIVALENCIAS.md, sección de calibración).
PATRON_CODIGO = re.compile(r"\b[a-z](?=[a-z0-9]{3,}\b)(?=[a-z0-9]*[0-9])[a-z0-9]{3,}\b", re.I)

# Un código que aparece en muchos productos distintos del catálogo no es
# el SKU de un producto puntual -- es un estándar de la industria (SCH40
# = cédula de tubería PVC, RJ45/CAT6 = conectores de red, IP65 = grado de
# protección). Se descarta por frecuencia real, calculada sobre el
# catálogo completo al construir el índice -- nunca una lista fija de
# provedor conocida de antemano.
MAX_APARICIONES_CODIGO_ESPECIFICO = 15

# Igual que reranking.py con PALABRAS_ACCESORIO, pero acá para un
# problema distinto: un repuesto/accesorio para una herramienta puede
# compartir literalmente el código de modelo de la herramienta base
# ("Repuesto carbón para demoledor ULDBRH1301" vs. el demoledor
# ULDBRH1301 en sí) sin ser el mismo producto comprable. Caso real
# encontrado calibrando contra el catálogo (ver EQUIVALENCIAS.md).
PALABRAS_PARTE = {
    "repuesto", "repuestos", "accesorio", "accesorios", "kit",
    "refaccion", "refacciones", "reparacion", "reparaciones",
    "empaque", "empaques", "junta", "juntas", "cubierta", "cubiertas", "cubre",
}

# Umbral de tokens para confirmar equivalencia SIN marca ni código
# compartido -- deliberadamente alto (a diferencia de similares.py, que
# solo tiene que ser "razonablemente parecido"; acá un falso positivo
# significa decirle a alguien que dos productos son literalmente el
# mismo artículo).
MINIMO_TOKENS_SIN_MARCA_NI_CODIGO = 3
JACCARD_MINIMO_SIN_MARCA_NI_CODIGO = 0.5

# Umbral para confirmar CON marca compartida. Calibrado contra un bug
# real encontrado corriendo el motor sobre el catálogo completo: exigir
# solo "marca igual + 2 tokens compartidos" agrupó 1,154 herramientas
# Truper distintas (alicates, llaves, martillos...) en un solo "grupo de
# equivalencia", porque CUALQUIER par de productos Truper comparte la
# palabra "truper" (contada dos veces: como marca Y como token) más UNA
# palabra genérica de categoría ("alicate"). Exigir además una
# proporción alta de solapamiento (jaccard), no solo una cuenta mínima,
# es lo que distingue "Alicate Articulado 10 Truper" de "Alicate Corte
# Diagonal 5 Truper" (mismo tipo de herramienta, tokens muy distintos)
# de dos tamaños/colores del MISMO producto (tokens casi idénticos salvo
# el que cambia).
MINIMO_TOKENS_INDEPENDIENTES_CON_MARCA = 2
JACCARD_MINIMO_CON_MARCA = 0.66


def extraer_codigos(nombre):
    """Candidatos a código de modelo/fabricante embebidos en el nombre.
    Devuelve el set crudo -- el filtrado por frecuencia (qué códigos son
    en realidad estándares genéricos) pasa aparte, en el índice, porque
    requiere ver el catálogo completo, no un nombre a la vez."""

    if not nombre:
        return set()
    return {m.group(0).upper() for m in PATRON_CODIGO.finditer(nombre)}


def extraer_tokens_identidad(nombre):
    """Tokens del nombre que describen QUÉ ES el producto, no su tamaño/
    unidad/cantidad -- misma exclusión que similares.py (TOKENS_GENERICOS,
    reutilizado tal cual) más números sueltos, para no calificar a dos
    productos como el mismo solo por compartir "1", "2" o "pulg"."""

    tokens = tokenizar(normalizar_texto(nombre or ""))
    return {t for t in tokens if t not in TOKENS_GENERICOS and not t.replace(".", "").isdigit()}


# familias.py (PRESENTACION_LABELS) reconoce la presentación de pintura
# cuando está escrita como palabra ("cuarto", "galón", "cubeta") -- pero
# no la notación en fracción de galón entre paréntesis que sí usa
# Ferretería Brenes en un tercio de sus pinturas, ej. "PV361 (1/4)" (=
# un cuarto de galón). Sin esto, dos tamaños DISTINTOS del mismo Barniz
# Lanco terminaban en el mismo grupo de equivalencia porque el lado de
# Brenes no reportaba ninguna presentación detectada (caso real
# encontrado corriendo el motor contra el catálogo completo). Es
# notación estándar de la industria de pinturas, no un patrón inventado
# para Brenes específicamente -- cualquier proveedor podría usarla.
_FRACCIONES_GALON = {
    # Solo 1/4 se mapea a "Cuarto" -- es la equivalencia real confirmada
    # contra el catálogo (mismo producto, Brenes lo escribe "(1/4)" donde
    # los demás escriben "Cuarto"). El resto de fracciones se deja con su
    # propia etiqueta -- 1/8 y 1/32 son presentaciones más chicas que un
    # cuarto, no lo mismo; agruparlas junto a "Cuarto" sería inventar una
    # equivalencia que no está confirmada.
    "1/4": "Cuarto",
    "1/2": "Medio galón",
    "1/8": "Octavo de galón",
    "1/16": "Dieciseisavo de galón",
    "1/32": "Treintaidosavo de galón",
}
# Los paréntesis son opcionales -- Brenes también escribe la fracción
# suelta al final, sin unidad ("WF828-5  1/4"), un caso real donde ni la
# etiqueta de familias.py (no reconoce fracciones, ver arriba) ni
# volumen_l (exige la palabra "galon"/"gal" pegada al número) capturan
# nada, dejando dos tamaños distintos indistinguibles (caso real: línea
# "Masilla Wood Filler" de Lanco, ver EQUIVALENCIAS.md).
_PATRON_FRACCION_GALON = re.compile(r"\(?\b(1/32|1/16|1/8|1/4|1/2)\b\)?")

# familias.analizar_nombre() (no se toca -- ver arriba) trata CUALQUIER
# número suelto del nombre como parte de la presentación, asumiendo que
# es un tamaño de empaque (1, 5, 55 galones...). Eso es correcto para su
# uso original, pero un proveedor puede tener además un número de línea
# ("Latex 3000") o un código interno ("0900070006") suelto en el nombre,
# que terminan mezclados en la misma etiqueta ("3000 Galón" o "9000
# Galón 0900070006"). Ningún tamaño de empaque real de este catálogo
# tiene 4+ dígitos, así que se descartan esos tokens de la etiqueta
# (no la etiqueta completa -- "3000 Galón" vs "3000 Cuarto" siguen
# siendo presentaciones distintas una vez quitado el "3000" de ambas;
# caso real: "Corrostop" de El Lagar vs. Construplaza, donde SÍ hay que
# descartar la etiqueta completa porque sin el código no queda ninguna
# palabra de unidad reconocida del lado de Construplaza).
_PATRON_NUMERO_LARGO = re.compile(r"\b\d{4,}\b")


def _detectar_presentacion_pintura(nombre):
    # La fracción se revisa PRIMERO, antes que familias.py: cuando el
    # nombre trae "galon"/"gal" como palabra reconocida, familias.py ya
    # devuelve una etiqueta no vacía ("Galón") y nunca se llega a revisar
    # la fracción -- perdiendo que "1/16 galon" y "1/32 galon" son
    # tamaños distintos, ambos etiquetados igual ("Galón" a secas).
    coincidencia = _PATRON_FRACCION_GALON.search(nombre or "")
    if coincidencia:
        return _FRACCIONES_GALON.get(coincidencia.group(1))

    presentacion = extraer_presentacion_pintura(nombre)
    if presentacion:
        limpia = _PATRON_NUMERO_LARGO.sub("", presentacion).strip()
        return limpia or None

    return None


# Color: igual idea que compatibilidad de medidas (SPECS_COMPATIBILIDAD),
# pero categórico, no numérico -- especificaciones.py solo captura specs
# con unidad pegada a un número, un color es una palabra suelta. Bug real
# corriendo el motor completo: "Rojo Óxido" y "Verde Óxido" comparten la
# palabra "óxido" (acabado, no color), lo que sube el solapamiento de
# tokens lo suficiente para confirmar como si fueran el mismo producto --
# dos colores nunca son el mismo producto comprable, sin importar cuánto
# más coincida el resto del nombre. No es propio de Pinturas: bombillos,
# tejas, herramientas, cualquier categoría con variantes de color corre el
# mismo riesgo, así que se revisa siempre, no solo con categoria=="Pinturas".
COLORES = {
    "rojo", "verde", "azul", "amarillo", "blanco", "negro", "gris",
    "cafe", "naranja", "morado", "violeta", "rosado", "rosa", "dorado",
    "plateado", "beige", "celeste", "turquesa", "marron", "vino",
    "crema", "aluminio", "plata", "oro", "bronce", "cobre",
    # "teja"/"ladrillo" (tonos terracota/ladrillo) -- nombres de color
    # reales de la industria de pinturas, no jerga de un proveedor (caso
    # real: línea Corrostop, donde son la única palabra que distingue
    # variantes que de otro modo comparten todo el resto del nombre).
    "teja", "ladrillo",
    # "marfil"/"champagne" -- acabados eléctricos (interruptores, tomas,
    # placas) tan comunes en este catálogo como blanco/negro, pero
    # ausentes de la lista original -- sin ellos, dos colores realmente
    # distintos ("negro" vs "marfil") quedan asimétricos (uno con color
    # detectado, el otro no) y el conflicto duro nunca se dispara. Caso
    # real encontrado en la auditoría: interruptor Eagle UL6501-BK
    # (negro) fusionado con UL6501-V (marfil); toma TV Bticino con 4
    # colores (negro/blanco/champagne/marfil) fusionados en un solo grupo
    # (ver EQUIVALENCIAS.md).
    "marfil", "champagne",
}


def extraer_colores(tokens_identidad):
    return tokens_identidad & COLORES


def es_parte_o_accesorio(tokens_identidad):
    """¿El nombre describe un repuesto/accesorio/kit de reparación, no el
    producto base? Se mira solo entre los primeros tokens de identidad --
    "Repuesto..." casi siempre abre el nombre; buscarlo en cualquier
    posición daría falsos positivos con productos que mencionan
    "repuestos" de pasada en la descripción del propio nombre."""

    return bool(tokens_identidad & PALABRAS_PARTE)


def extraer_atributos(nombre, marca, categoria=None):
    """Toda la huella de identidad de un producto en un solo dict,
    calculada una vez y reutilizable para compararlo contra cualquier
    candidato. `categoria` solo se usa para decidir si vale la pena
    revisar presentación de pintura (familias.py) -- no participa en la
    comparación de specs en sí."""

    tokens = extraer_tokens_identidad(nombre)

    return {
        "marca": (marca or "").strip().lower() or None,
        "categoria": (categoria or "").strip().lower() or None,
        "codigos": extraer_codigos(nombre),
        "specs": extraer_specs(nombre),
        "presentacion_pintura": (
            _detectar_presentacion_pintura(nombre) if categoria == "Pinturas" else None
        ),
        "colores": extraer_colores(tokens),
        "tokens": tokens,
        "es_parte": es_parte_o_accesorio(tokens),
    }


def comparar_atributos(atributos_a, atributos_b, codigos_genericos=frozenset()):
    """Compara dos huellas ya extraídas (ver extraer_atributos). Devuelve
    (veredicto, razones, detalle) -- nunca lanza, nunca inventa una
    coincidencia que no está respaldada por al menos una señal real.

    `codigos_genericos`: set de códigos a ignorar como señal porque
    resultaron ser estándares de la industria, no SKUs puntuales (ver
    MAX_APARICIONES_CODIGO_ESPECIFICO) -- calculado por el índice sobre
    el catálogo completo, vacío por defecto para poder probar esta
    función de forma aislada."""

    razones = []

    specs_cmp = comparar_specs(atributos_a["specs"], atributos_b["specs"])
    if specs_cmp["conflicto"]:
        return NO_EQUIVALENTE, [f"specs_en_conflicto:{','.join(specs_cmp['conflicto_en'])}"], specs_cmp

    pres_a, pres_b = atributos_a["presentacion_pintura"], atributos_b["presentacion_pintura"]
    if pres_a and pres_b and pres_a != pres_b:
        return NO_EQUIVALENTE, [f"presentacion_distinta:{pres_a}_vs_{pres_b}"], specs_cmp

    colores_a, colores_b = atributos_a["colores"], atributos_b["colores"]
    if colores_a and colores_b and not (colores_a & colores_b):
        razon = f"color_distinto:{','.join(sorted(colores_a))}_vs_{','.join(sorted(colores_b))}"
        return NO_EQUIVALENTE, [razon], specs_cmp

    codigos_compartidos = (atributos_a["codigos"] & atributos_b["codigos"]) - codigos_genericos

    # Un código descartado por genérico (MR11, GU10 -- estándares de forma
    # de bombillo, no un SKU puntual, ver MAX_APARICIONES_CODIGO_ESPECIFICO)
    # no deja de ser genérico solo porque tokenizar() también lo capture
    # como palabra suelta -- sin esto, dos bombillos de watts y
    # temperatura de color totalmente distintos confirmaban por compartir
    # "bombillo,led,mr11" como tokens (caso real encontrado corriendo el
    # motor completo). Se descarta del canal de tokens igual que del canal
    # de código.
    codigos_genericos_minuscula = {c.lower() for c in codigos_genericos}
    tokens_a = atributos_a["tokens"] - codigos_genericos_minuscula
    tokens_b = atributos_b["tokens"] - codigos_genericos_minuscula

    tokens_compartidos = tokens_a & tokens_b
    union_tokens = tokens_a | tokens_b
    jaccard = len(tokens_compartidos) / len(union_tokens) if union_tokens else 0.0

    # El propio código o la propia marca casi siempre aparecen también
    # como token normal del nombre (tokenizar() no sabe que "SP02" es un
    # código o que "Truper" ya se comparó aparte como marca) -- contarlos
    # de nuevo como "token de refuerzo" sería la misma señal contada dos
    # veces, no evidencia independiente. Se restan antes de usar
    # tokens_compartidos como corroboración de un código o de una marca.
    marca_a, marca_b = atributos_a["marca"], atributos_b["marca"]
    marca_coincide = bool(marca_a) and marca_a == marca_b

    señales_no_independientes = {c.lower() for c in codigos_compartidos}
    if marca_coincide:
        # marca_a puede ser una marca de varias palabras ("National
        # Hardware", "Black & Decker") -- tokens_compartidos son palabras
        # sueltas, así que agregar el string completo de la marca nunca
        # las alcanza a excluir. Sin esto, cada palabra de una marca
        # multi-palabra queda contando como "token de refuerzo
        # independiente" en todos los pares de esa marca (caso real:
        # "National Hardware", ver EQUIVALENCIAS.md).
        señales_no_independientes |= set(tokenizar(normalizar_texto(marca_a)))
    tokens_compartidos_independientes = tokens_compartidos - señales_no_independientes

    # jaccard "independiente": igual que con los tokens de refuerzo, si
    # la marca es idéntica en ambos (el caso más común acá) contarla dentro
    # del jaccard infla el parecido artificialmente en ambos lados a la
    # vez -- "Barniz Aquavar Caoba Galon Lanco" vs "...Nuez Galon Lanco"
    # da 0.667 (por encima del umbral) CON "lanco" adentro, y 0.6 (por
    # debajo, correctamente) sin él. Se recalcula sobre los sets ya sin
    # marca/código para la corroboración de marca.
    tokens_a_independientes = tokens_a - señales_no_independientes
    tokens_b_independientes = tokens_b - señales_no_independientes
    union_independiente = tokens_a_independientes | tokens_b_independientes
    jaccard_independiente = (
        len(tokens_compartidos_independientes) / len(union_independiente) if union_independiente else 0.0
    )

    una_es_parte_y_otra_no = atributos_a["es_parte"] != atributos_b["es_parte"]

    if marca_coincide:
        razones.append(f"misma_marca:{marca_a}")
    if codigos_compartidos:
        razones.append(f"codigo_compartido:{','.join(sorted(codigos_compartidos))}")
    if tokens_compartidos:
        razones.append(f"tokens_compartidos:{','.join(sorted(tokens_compartidos))}")
    if una_es_parte_y_otra_no:
        razones.append("asimetria_repuesto_vs_producto_base")

    detalle = {**specs_cmp, "jaccard_tokens": round(jaccard, 3), "tokens_compartidos": len(tokens_compartidos)}

    if codigos_compartidos and not una_es_parte_y_otra_no:
        # Código de fabricante compartido es la señal más fuerte que hay
        # -- pero un código corto (4 caracteres) puede coincidir por
        # casualidad entre dos fabricantes sin relación (caso real
        # encontrado calibrando: "SP02" en un dispensador de jabón Y en
        # un soplete de gas), o ser el prefijo de TODA una línea de
        # productos del mismo fabricante, no el SKU de uno puntual (caso
        # real: "N400" es compartido por decenas de bisagras National
        # Hardware de tamaños y acabados distintos -- bastaba con que la
        # marca también coincidiera para confirmar, sin exigir ninguna
        # palabra descriptiva de refuerzo). Un código de 5+ caracteres es
        # ya mucho menos propenso a cualquiera de los dos problemas y
        # confirma solo; uno corto exige la misma evidencia de texto que
        # el camino sin código (ver JACCARD_MINIMO_CON_MARCA).
        codigo_largo = any(len(c) >= 5 for c in codigos_compartidos)
        if codigo_largo:
            return EQUIVALENCIA_CONFIRMADA, razones, detalle
        if (
            len(tokens_compartidos_independientes) >= MINIMO_TOKENS_INDEPENDIENTES_CON_MARCA
            and jaccard_independiente >= JACCARD_MINIMO_CON_MARCA
        ):
            return EQUIVALENCIA_CONFIRMADA, razones, detalle
        return EQUIVALENCIA_PROBABLE, razones, detalle

    if codigos_compartidos and una_es_parte_y_otra_no:
        # Mismo código, pero uno es un repuesto/accesorio para el modelo
        # del otro -- relacionados de verdad, pero no el mismo artículo
        # comprable.
        return EQUIVALENCIA_PROBABLE, razones, detalle

    if marca_coincide and (
        len(tokens_compartidos_independientes) >= MINIMO_TOKENS_INDEPENDIENTES_CON_MARCA
        and jaccard_independiente >= JACCARD_MINIMO_CON_MARCA
    ):
        return EQUIVALENCIA_CONFIRMADA, razones, detalle

    if marca_coincide and tokens_compartidos_independientes:
        return EQUIVALENCIA_PROBABLE, razones, detalle

    if (
        not marca_coincide
        and len(tokens_compartidos) >= MINIMO_TOKENS_SIN_MARCA_NI_CODIGO
        and jaccard >= JACCARD_MINIMO_SIN_MARCA_NI_CODIGO
    ):
        # Sin marca ni código en ningún lado -- puede pasar (EPA/Brenes no
        # tienen marca en un tercio del catálogo). Se exige mucha más
        # evidencia de nombre para compensar la ausencia total de señal
        # estructurada.
        return EQUIVALENCIA_PROBABLE, razones, detalle

    return NO_EQUIVALENTE, razones, detalle


# ============================================================================
# Motor de confianza -- puntaje continuo y explicable
# ============================================================================
#
# comparar_atributos() de arriba (sin tocar, sigue con sus propias pruebas)
# responde con un veredicto de tres niveles mediante una cascada de reglas
# if/elif calibradas a mano. calcular_puntaje_equivalencia() responde la
# misma pregunta de forma distinta: un puntaje continuo [0, 1] construido
# como promedio ponderado de diez señales nombradas, cada una explicable
# por separado -- para que cada consumidor (comparador, similares,
# presupuestos, cotizaciones, búsqueda) pueda fijar su propio umbral según
# qué tan caro le sale un falso positivo, en vez de heredar un único punto
# de corte pensado para "el mismo artículo, sin excepción".
#
# No es una heurística nueva: reutiliza exactamente la misma extracción de
# atributos (extraer_atributos) y las mismas correcciones ya calibradas
# contra el catálogo real (EQUIVALENCIAS.md, AUDITORIA_EQUIVALENCIAS.md).
# Lo que cambia es solo cómo se combinan las señales.
#
# Diseño: cinco señales son de VETO (dimensiones, tamaño, volumen,
# presentación, color) -- si cualquiera está en conflicto real (ambos
# lados tienen dato y difieren), el puntaje es 0.0 sin excepción. Son
# incompatibilidades físicas u objetivas, no evidencia débil que otra
# señal fuerte pueda compensar: un tubo de 1/2" nunca es el mismo
# artículo que uno de 3/4", sin importar cuánto coincida el resto del
# nombre. Las otras cinco (marca, sku, código de fabricante, tokens,
# categoría) son evidencia acumulativa y ponderada.

PESOS_SEÑALES = {
    "codigo_fabricante": 0.28,
    "marca": 0.20,
    "tokens": 0.20,
    "sku": 0.12,
    "categoria": 0.05,
    "dimensiones": 0.03,
    "tamano": 0.03,
    "volumen": 0.03,
    "presentacion": 0.03,
    "color": 0.03,
}

SEÑALES_VETO = frozenset({"dimensiones", "tamano", "volumen", "presentacion", "color"})

# "codigo_fabricante" es la única señal ancla: probado empíricamente
# contra el catálogo real (AUDITORIA_EQUIVALENCIAS.md -- "cada grupo
# confirmado por un código de fabricante completo fue correcto") que un
# código de 5+ caracteres compartido es, por sí solo, casi siempre
# suficiente. Un promedio ponderado plano no puede capturar eso: si dos
# proveedores describen el mismo producto con palabras completamente
# distintas ("Broca SDS Plus... Dewalt" vs "DW Broca p/rotomartillo...",
# mismo código DW5402), el jaccard de tokens es bajísimo (1/7) y diluiría
# la señal fuerte del código hasta un puntaje mediocre -- exactamente el
# error que ya se corrigió una vez con "codigo_largo confirma solo" en
# comparar_atributos. Acá se resuelve igual pero de forma graduable: si
# hay código de fabricante, el resto de las señales (corroboración) solo
# puede MOVER el puntaje alrededor de 1.0 dentro de un rango acotado
# (BONUS_CORROBORACION), nunca diluirlo con un promedio plano. Sin código
# de fabricante, todas las demás señales SÍ se combinan por promedio
# ponderado normal (ninguna otra demostró ser confiable por sí sola).
SEÑAL_ANCLA = "codigo_fabricante"
BONUS_CORROBORACION = 0.15

# Un repuesto/accesorio y su producto base pueden compartir marca, código
# y casi todos los tokens sin ser el mismo artículo comprable (ver
# PALABRAS_PARTE) -- no es una señal nombrada con peso propio (el usuario
# pidió diez señales explícitas), es un ajuste que se aplica DESPUÉS del
# promedio ponderado, igual que ya hacía comparar_atributos con su rama
# "codigos_compartidos and una_es_parte_y_otra_no". Queda documentado en
# el resultado, nunca oculto.
PENALIZACION_ASIMETRIA_PARTE = 0.5

_CLAVES_DIMENSIONES = SPECS_COMPATIBILIDAD | SPECS_RENDIMIENTO
_CLAVES_TAMANO = SPECS_UNIDAD_VENTA - {"volumen_l"}

# diametro_mm y diametro_pulg son la MISMA magnitud física en unidades
# distintas, pero especificaciones.py los guarda en campos separados y
# nunca los compara entre sí (hallazgo real de AUDITORIA_EQUIVALENCIAS.md:
# escuadras, candados y tubería PVC donde un proveedor usa pulgadas y otro
# milímetros para el mismo tamaño quedaban invisibles entre sí). Se
# convierte a una escala común solo acá, para esta comparación cruzada --
# no se toca especificaciones.py ni su semántica ya usada por
# presupuestos.py. Tolerancia del 15% porque son tallas nominales
# redondeadas, no medidas exactas -- el propio catálogo ya describe la
# misma tubería como "12 mm (1/2\")", y 1/2" real son 12.7 mm.
_MM_POR_PULGADA = 25.4
_TOLERANCIA_DIAMETRO_MM_PULG = 0.15


def _diametro_cruzado(specs_a, specs_b):
    """Compara diametro_pulg de un lado contra diametro_mm del otro (y
    viceversa), convertido a una escala común. Devuelve "conflicto",
    "coincide" o None (sin datos cruzados para comparar)."""

    for pulg, mm in ((specs_a.get("diametro_pulg"), specs_b.get("diametro_mm")),
                      (specs_b.get("diametro_pulg"), specs_a.get("diametro_mm"))):
        if pulg is None or mm is None:
            continue
        mm_equivalente = pulg * _MM_POR_PULGADA
        if abs(mm_equivalente - mm) / mm_equivalente <= _TOLERANCIA_DIAMETRO_MM_PULG:
            return "coincide"
        return "conflicto"
    return None


def _señal_veto(conflicto, coincide, detalle_conflicto, detalle_coincide):
    if conflicto:
        return {"valor": 0.0, "conflicto": True, "detalle": detalle_conflicto}
    if coincide:
        return {"valor": 1.0, "conflicto": False, "detalle": detalle_coincide}
    return {"valor": None, "conflicto": False, "detalle": "sin datos en ambos lados"}


def _señal_codigos(atributos_a, atributos_b, codigos_genericos):
    """Separa la evidencia de código en dos señales distintas -- un
    código de 5+ caracteres es, medido contra el catálogo real, casi
    nunca casual (ver AUDITORIA_EQUIVALENCIAS.md: "cada grupo confirmado
    por un código de fabricante completo fue correcto"); uno corto sí
    puede coincidir por azar entre productos sin relación (caso real:
    "SP02" en un dispensador de jabón y un soplete de gas) o ser el
    prefijo de toda una línea de un fabricante, no el SKU de un producto
    puntual (caso real: "N400" en decenas de bisagras National Hardware
    de tamaños distintos) -- por eso pesa bastante menos."""

    codigos_compartidos = (atributos_a["codigos"] & atributos_b["codigos"]) - codigos_genericos
    if not codigos_compartidos:
        vacio = {"valor": None, "conflicto": False, "detalle": "sin código compartido"}
        return vacio, dict(vacio), codigos_compartidos

    largos = {c for c in codigos_compartidos if len(c) >= 5}
    cortos = codigos_compartidos - largos

    fabricante = (
        {"valor": 1.0, "conflicto": False, "detalle": f"código de fabricante compartido: {','.join(sorted(largos))}"}
        if largos
        else {"valor": None, "conflicto": False, "detalle": "sin código de 5+ caracteres"}
    )
    sku = (
        {"valor": 1.0, "conflicto": False, "detalle": f"código corto compartido: {','.join(sorted(cortos))}"}
        if cortos and not largos
        else {"valor": None, "conflicto": False, "detalle": "sin código corto adicional"}
    )
    return fabricante, sku, codigos_compartidos


def _promedio_ponderado(señales, nombres):
    presentes = [(n, señales[n]["valor"]) for n in nombres if señales[n]["valor"] is not None]
    if not presentes:
        return None
    peso_total = sum(PESOS_SEÑALES[n] for n, _ in presentes)
    return sum(PESOS_SEÑALES[n] * valor for n, valor in presentes) / peso_total


def _combinar_señales(señales):
    """Combina las señales ya calculadas (ninguna en veto) en un único
    puntaje [0, 1]. Ver el comentario de SEÑAL_ANCLA para el porqué de
    este diseño de dos ramas en vez de un promedio ponderado plano único."""

    otras = [n for n in PESOS_SEÑALES if n != SEÑAL_ANCLA]
    corroboracion = _promedio_ponderado(señales, otras)

    if señales[SEÑAL_ANCLA]["valor"] is None:
        # Sin código de fabricante: todas las demás señales se combinan
        # por promedio ponderado normal, igual que cualquier otra señal
        # -- ninguna de ellas demostró ser confiable por sí sola.
        return corroboracion if corroboracion is not None else 0.0

    ancla = señales[SEÑAL_ANCLA]["valor"]
    if corroboracion is None:
        return ancla
    # corroboracion=0.5 es neutral (no mueve nada); por encima empuja el
    # puntaje hacia 1.0, por debajo lo empuja hacia abajo -- pero nunca
    # más de BONUS_CORROBORACION en cualquier dirección, así que un
    # código de fabricante compartido nunca puede quedar en cero solo
    # porque la marca sea distinta o los tokens no calcen.
    ajuste = (corroboracion - 0.5) * BONUS_CORROBORACION * 2
    return max(0.0, min(1.0, ancla + ajuste))


def calcular_puntaje_equivalencia(atributos_a, atributos_b, codigos_genericos=frozenset()):
    """Puntaje de confianza [0, 1] de que dos huellas ya extraídas (ver
    extraer_atributos) son el mismo producto comercial, más el desglose
    completo de cómo se llegó a ese número -- señal por señal, cada una
    con su valor, su peso y una razón en texto. Nunca inventa evidencia:
    una señal sin datos de ningún lado queda en valor=None y no participa
    del promedio (ausencia no es lo mismo que desacuerdo).

    Devuelve un dict:
        puntaje: float en [0, 1]
        veto: nombre de la primera señal en conflicto duro, o None
        señales: {nombre: {valor, conflicto, detalle}} para las diez
            señales (más "ajuste_repuesto" cuando aplica)
        explicacion: resumen en una línea, en español, de por qué

    `codigos_genericos`: igual que en comparar_atributos -- códigos que
    resultaron ser estándares de industria, no SKUs puntuales."""

    señales = {}

    specs_cmp = comparar_specs(atributos_a["specs"], atributos_b["specs"])
    conflicto_en = set(specs_cmp["conflicto_en"])
    coincidencias = set(specs_cmp["coincidencias"])

    cruce_mm_pulg = _diametro_cruzado(atributos_a["specs"], atributos_b["specs"])
    dim_conflicto_en = conflicto_en & _CLAVES_DIMENSIONES
    señales["dimensiones"] = _señal_veto(
        conflicto=bool(dim_conflicto_en) or cruce_mm_pulg == "conflicto",
        coincide=bool(coincidencias & _CLAVES_DIMENSIONES) or cruce_mm_pulg == "coincide",
        detalle_conflicto=f"medida física en conflicto: {','.join(sorted(dim_conflicto_en)) or 'diámetro mm vs pulgadas'}",
        detalle_coincide="medida física coincide",
    )

    señales["tamano"] = _señal_veto(
        conflicto=bool(conflicto_en & _CLAVES_TAMANO),
        coincide=bool(coincidencias & _CLAVES_TAMANO),
        detalle_conflicto=f"tamaño de envase en conflicto: {','.join(sorted(conflicto_en & _CLAVES_TAMANO))}",
        detalle_coincide="tamaño de envase coincide",
    )

    señales["volumen"] = _señal_veto(
        conflicto="volumen_l" in conflicto_en,
        coincide="volumen_l" in coincidencias,
        detalle_conflicto="volumen en conflicto",
        detalle_coincide="volumen coincide",
    )

    pres_a, pres_b = atributos_a["presentacion_pintura"], atributos_b["presentacion_pintura"]
    señales["presentacion"] = _señal_veto(
        conflicto=bool(pres_a and pres_b and pres_a != pres_b),
        coincide=bool(pres_a and pres_b and pres_a == pres_b),
        detalle_conflicto=f"presentación distinta: {pres_a} vs {pres_b}",
        detalle_coincide=f"presentación coincide: {pres_a}",
    )

    colores_a, colores_b = atributos_a["colores"], atributos_b["colores"]
    señales["color"] = _señal_veto(
        conflicto=bool(colores_a and colores_b and not (colores_a & colores_b)),
        coincide=bool(colores_a and colores_b and (colores_a & colores_b)),
        detalle_conflicto=f"color distinto: {','.join(sorted(colores_a))} vs {','.join(sorted(colores_b))}",
        detalle_coincide=f"color coincide: {','.join(sorted(colores_a & colores_b))}",
    )

    veto = next((nombre for nombre in SEÑALES_VETO if señales[nombre]["conflicto"]), None)

    señales["codigo_fabricante"], señales["sku"], codigos_compartidos = _señal_codigos(
        atributos_a, atributos_b, codigos_genericos
    )

    marca_a, marca_b = atributos_a["marca"], atributos_b["marca"]
    marca_coincide = bool(marca_a) and marca_a == marca_b
    if not marca_a or not marca_b:
        señales["marca"] = {"valor": None, "conflicto": False, "detalle": "marca ausente en uno o ambos lados"}
    elif marca_coincide:
        señales["marca"] = {"valor": 1.0, "conflicto": False, "detalle": f"marca coincide: {marca_a}"}
    else:
        señales["marca"] = {"valor": 0.0, "conflicto": False, "detalle": f"marca distinta: {marca_a} vs {marca_b}"}

    categoria_a, categoria_b = atributos_a.get("categoria"), atributos_b.get("categoria")
    if not categoria_a or not categoria_b:
        señales["categoria"] = {"valor": None, "conflicto": False, "detalle": "categoría ausente en uno o ambos lados"}
    elif categoria_a == categoria_b:
        señales["categoria"] = {"valor": 1.0, "conflicto": False, "detalle": f"categoría coincide: {categoria_a}"}
    else:
        señales["categoria"] = {
            "valor": 0.0,
            "conflicto": False,
            # Peso deliberadamente bajo (0.05): la categoría es la señal
            # menos confiable del catálogo real -- Construplaza etiqueta
            # su línea de pegamento PVC como "Plomería" mientras el resto
            # del catálogo la trata como "Pinturas" (ver
            # AUDITORIA_EQUIVALENCIAS.md), así que un desacuerdo acá no
            # dice mucho.
            "detalle": f"categoría distinta: {categoria_a} vs {categoria_b}",
        }

    # Igual que comparar_atributos: el código y la marca compartidos casi
    # siempre aparecen también como token normal del nombre -- contarlos
    # de nuevo como evidencia de "tokens" sería la misma señal contada dos
    # veces. Se restan antes de calcular el jaccard.
    codigos_genericos_minuscula = {c.lower() for c in codigos_genericos}
    tokens_a = atributos_a["tokens"] - codigos_genericos_minuscula
    tokens_b = atributos_b["tokens"] - codigos_genericos_minuscula

    señales_no_independientes = {c.lower() for c in codigos_compartidos}
    if marca_coincide:
        señales_no_independientes |= set(tokenizar(normalizar_texto(marca_a)))

    tokens_a_independientes = tokens_a - señales_no_independientes
    tokens_b_independientes = tokens_b - señales_no_independientes
    tokens_compartidos_independientes = tokens_a_independientes & tokens_b_independientes
    union_independiente = tokens_a_independientes | tokens_b_independientes

    if union_independiente:
        jaccard_independiente = len(tokens_compartidos_independientes) / len(union_independiente)
        señales["tokens"] = {
            "valor": round(jaccard_independiente, 4),
            "conflicto": False,
            "detalle": (
                f"{len(tokens_compartidos_independientes)} tokens compartidos de {len(union_independiente)} "
                f"({','.join(sorted(tokens_compartidos_independientes)) or 'ninguno'})"
            ),
        }
    else:
        señales["tokens"] = {"valor": None, "conflicto": False, "detalle": "sin tokens de identidad en ningún lado"}

    if veto:
        puntaje = 0.0
    else:
        puntaje = _combinar_señales(señales)

    una_es_parte_y_otra_no = atributos_a["es_parte"] != atributos_b["es_parte"]
    if una_es_parte_y_otra_no and not veto:
        puntaje *= PENALIZACION_ASIMETRIA_PARTE
        señales["ajuste_repuesto"] = {
            "valor": None,
            "conflicto": False,
            "detalle": f"un lado es repuesto/accesorio y el otro no -- puntaje reducido a {round(puntaje, 4)}",
        }

    return {
        "puntaje": round(puntaje, 4),
        "veto": veto,
        "señales": señales,
        "explicacion": _explicar_puntaje(señales, veto, puntaje),
    }


def _explicar_puntaje(señales, veto, puntaje):
    if veto:
        return f"No equivalente ({puntaje:.2f}): {señales[veto]['detalle']}."

    a_favor = [
        s["detalle"]
        for nombre, s in señales.items()
        if nombre != "ajuste_repuesto" and s["valor"] not in (None, 0.0)
    ]
    en_contra = [
        s["detalle"]
        for nombre, s in señales.items()
        if nombre not in SEÑALES_VETO and nombre != "ajuste_repuesto" and s["valor"] == 0.0
    ]
    ajuste = señales.get("ajuste_repuesto")

    partes = [f"Puntaje {puntaje:.2f}"]
    if a_favor:
        partes.append("a favor: " + "; ".join(a_favor))
    if en_contra:
        partes.append("en contra: " + "; ".join(en_contra))
    if ajuste:
        partes.append(ajuste["detalle"])
    return " -- ".join(partes) + "."


# Umbral por módulo: cada consumidor decide qué tan seguro tiene que estar
# el motor antes de tratar dos productos como el mismo artículo, según qué
# tan caro sale ahí un falso positivo. Calibrado contra
# AUDITORIA_EQUIVALENCIAS.md (ver MOTOR_CONFIANZA.md para el detalle
# completo de la calibración) -- todavía NO está conectado a ningún
# consumidor real (búsqueda, similares, comparador, presupuestos y
# cotizaciones no importan este módulo hoy); es la configuración que cada
# uno podrá usar cuando se integre, sin tener que re-derivar su propio
# umbral.
UMBRALES_POR_MODULO = {
    # Es solo una señal más entre varias para ordenar/agrupar resultados
    # -- un falso positivo acá cuesta, como mucho, un resultado de más.
    "busqueda": 0.45,
    "similares": 0.45,
    # Le muestra al usuario "esto es lo mismo" lado a lado -- more caro
    # que búsqueda, pero no involucra dinero.
    "comparador": 0.70,
    # Calcula un ahorro en dinero real a partir de tratar dos productos
    # como intercambiables -- el umbral más alto, deliberadamente cerca
    # de "requiere código de fabricante o marca+tokens muy fuertes" (ver
    # AUDITORIA_EQUIVALENCIAS.md: "restringir a coincidencia por código
    # de fabricante específico").
    "presupuestos": 0.85,
    "cotizaciones": 0.85,
}


def es_equivalente_para(modulo, puntaje):
    """¿Este puntaje alcanza el umbral que ese módulo específico definió
    para sí mismo? Lanza ValueError si el módulo no está en
    UMBRALES_POR_MODULO -- mejor fallar fuerte que asumir un umbral por
    defecto que nadie pidió."""

    if modulo not in UMBRALES_POR_MODULO:
        raise ValueError(f"módulo desconocido: {modulo!r} (ver UMBRALES_POR_MODULO)")
    return puntaje >= UMBRALES_POR_MODULO[modulo]


class _UnionFind:
    """Estructura mínima para agrupar productos transitivamente: si A
    equivale a B y B equivale a C, A/B/C terminan en el mismo grupo aunque
    A y C nunca se hayan comparado directamente entre sí."""

    def __init__(self):
        self.padre = {}

    def encontrar(self, x):
        self.padre.setdefault(x, x)
        while self.padre[x] != x:
            self.padre[x] = self.padre[self.padre[x]]
            x = self.padre[x]
        return x

    def unir(self, x, y):
        raiz_x, raiz_y = self.encontrar(x), self.encontrar(y)
        if raiz_x != raiz_y:
            self.padre[raiz_x] = raiz_y


def _candidatos_por_bloque(productos):
    """Genera pares candidatos SIN comparar todo contra todo (60,421^2 es
    inviable) -- bloquea por código compartido o por (marca + al menos un
    token compartido), vía índices invertidos sobre el catálogo COMPLETO.
    Nunca compara dos productos del mismo proveedor (la equivalencia es,
    por definición, entre proveedores distintos -- duplicados dentro de
    un mismo proveedor son un problema ya resuelto por el
    UNIQUE(proveedor, id_proveedor) de guardar_productos).

    Deliberadamente NO bloquea por categoría, a diferencia de una primera
    versión de este motor: medido contra el catálogo real, solo 10 de 751
    valores de categoria tienen productos de 2+ proveedores -- EPA y
    Carbone Store usan una taxonomía mucho más fina (cientos de valores
    específicos, ej. "Brocas para Concreto") que Construplaza/Novex/El
    Lagar/Brenes (unos 20-30 departamentos amplios, ej. "Herramientas").
    Exigir categoria idéntica dejaba a EPA y Carbone Store casi
    completamente afuera del índice, no porque no tuvieran productos
    equivalentes reales, sino porque sus propias categorías nunca
    coinciden en texto con las de nadie más. El código y la (marca +
    token) ya son señales lo bastante específicas por sí solas -- no
    hace falta la categoría como bloqueo adicional para mantener esto
    manejable."""

    indice_codigo = defaultdict(list)
    indice_token = defaultdict(list)

    for idx, p in enumerate(productos):
        for codigo in p["_atributos"]["codigos"]:
            indice_codigo[codigo].append(idx)
        if p["_atributos"]["marca"]:
            for token in p["_atributos"]["tokens"]:
                indice_token[(p["_atributos"]["marca"], token)].append(idx)

    vistos = set()

    for grupo_idx in list(indice_codigo.values()) + list(indice_token.values()):
        if len(grupo_idx) < 2:
            continue
        for i in range(len(grupo_idx)):
            for j in range(i + 1, len(grupo_idx)):
                a, b = productos[grupo_idx[i]], productos[grupo_idx[j]]
                if a["proveedor"] == b["proveedor"]:
                    continue
                clave = tuple(sorted((a["id"], b["id"])))
                if clave in vistos:
                    continue
                vistos.add(clave)
                yield a, b


def calcular_frecuencia_codigos(productos_con_atributos):
    """Cuenta en cuántos productos distintos aparece cada código
    candidato, sobre el catálogo completo -- la única forma de distinguir
    un SKU real (2-10 apariciones típicamente) de un estándar de industria
    (decenas a cientos, ver MAX_APARICIONES_CODIGO_ESPECIFICO)."""

    conteo = defaultdict(int)
    for p in productos_con_atributos:
        for codigo in p["_atributos"]["codigos"]:
            conteo[codigo] += 1
    return conteo


def calcular_equivalencias(productos, depurar=False):
    """Motor completo: recibe una lista de dicts con al menos
    {id, proveedor, nombre, marca, categoria}, y devuelve la lista de
    grupos de equivalencia encontrados -- cada uno con sus miembros y,
    si depurar=True, las razones de cada coincidencia que formó el grupo.

    No toca la base de datos -- la persistencia (tabla
    grupos_equivalencia + productos.equivalencia_id) vive en
    database/agregar_equivalencias.py, que sí puede llamar a esta función
    y guardar el resultado. Separado a propósito para poder probar el
    motor en memoria, con datos sintéticos, sin una base de datos real."""

    for p in productos:
        p["_atributos"] = extraer_atributos(p["nombre"], p.get("marca"), p.get("categoria"))

    frecuencia_codigos = calcular_frecuencia_codigos(productos)
    codigos_genericos = frozenset(
        codigo for codigo, n in frecuencia_codigos.items() if n > MAX_APARICIONES_CODIGO_ESPECIFICO
    )

    uf = _UnionFind()
    razones_por_par = {}

    for a, b in _candidatos_por_bloque(productos):
        veredicto, razones, _ = comparar_atributos(a["_atributos"], b["_atributos"], codigos_genericos)
        if veredicto == EQUIVALENCIA_CONFIRMADA:
            uf.unir(a["id"], b["id"])
            if depurar:
                razones_por_par[tuple(sorted((a["id"], b["id"])))] = razones

    grupos = defaultdict(list)
    for p in productos:
        if p["id"] in uf.padre:
            grupos[uf.encontrar(p["id"])].append(p)

    resultado = []
    for miembros in grupos.values():
        proveedores = {m["proveedor"] for m in miembros}
        if len(miembros) < 2 or len(proveedores) < 2:
            continue
        item = {
            "miembros": miembros,
            "cantidad_miembros": len(miembros),
            "cantidad_proveedores": len(proveedores),
        }
        if depurar:
            item["razones"] = razones_por_par
        resultado.append(item)

    for p in productos:
        del p["_atributos"]

    return resultado
