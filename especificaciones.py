"""Extracción y comparación de especificaciones técnicas (medida, calibre,
potencia, voltaje, peso, presentación) a partir del nombre de un producto.

Existe como módulo aparte de `similares.py` a propósito: `similares.py`
responde "¿qué más le puede interesar a alguien viendo esto?" (más laxo,
sirve para explorar); este módulo responde "¿es seguro tratar estos dos
productos como intercambiables para calcular un ahorro en dinero?" (mucho
más estricto -- un falso positivo acá significa mostrarle a alguien un
ahorro que no existe).

Deliberadamente NO reutiliza `normalizar_texto()` de busqueda.py para la
extracción: esa función colapsa fracciones ("1/2" -> "1 2") y borra el
símbolo "#" porque para tokenizar/buscar eso no importa -- para comparar
especificaciones sí importa (confirmado probando con nombres reales: sin
este cuidado, 'Taladro 1/2"' se leía como diámetro 2, no 0.5). La limpieza
de acentos/mayúsculas de este módulo es intencionalmente más liviana.

Validado contra nombres reales de las 6 categorías pedidas (cemento,
pintura, taladros, tubo PVC, tornillos, cable eléctrico) antes de fijar
estas reglas -- ver PRESUPUESTOS_INTELIGENTES.md para el detalle completo
de qué funcionó y qué no en cada categoría.
"""

import re
import unicodedata

from familias import analizar_nombre as _analizar_presentacion_pintura

# Specs de compatibilidad física: si ambos productos tienen un valor
# detectado para esta clave y difieren, son incompatibles sin excepción
# (un taladro de mandril 1/2" y uno de 3/8" no son sustitutos, sin importar
# qué tan parecido sea el resto del nombre).
SPECS_COMPATIBILIDAD = {"diametro_pulg", "calibre", "longitud_cm"}

# Specs de "unidad de venta": si ambos las tienen y difieren, tampoco son
# comparables (500 uds. no es lo mismo que 1000 uds.) -- pero si solo UNO
# de los dos productos tiene un valor detectado (asimetría), no se puede
# confirmar ni descartar con certeza, así que nunca alcanza el nivel más
# alto de confianza solo por esto (ver presupuestos.py).
SPECS_UNIDAD_VENTA = {"peso_kg", "peso_lb", "cantidad_unidades", "volumen_l"}

# Specs de rendimiento: se comparan con tolerancia, nunca descalifican por
# sí solas -- dos taladros de 750W y 710W siguen siendo sustitutos
# razonables.
SPECS_RENDIMIENTO = {"potencia_w", "voltaje", "longitud_m"}

TOLERANCIA_RENDIMIENTO = 0.20  # 20%, igual que el peso_similar de similares.py

# ============================================================================
# Motor de especificaciones -- Fase 2 (ver MOTOR_ESPECIFICACIONES_ANALISIS.md
# para la Fase 1: análisis del catálogo completo, 60,421 productos, que
# fundamenta cada patrón nuevo de acá abajo con frecuencia real y ejemplos
# de falso positivo ya verificados).
#
# Estas specs YA se extraen (quedan en el dict que devuelve extraer_specs())
# pero DELIBERADAMENTE no se agregan todavía a SPECS_COMPATIBILIDAD /
# SPECS_UNIDAD_VENTA / SPECS_RENDIMIENTO ni a TODAS_LAS_SPECS -- eso
# activaría un veto nuevo en comparar_specs(), y por lo tanto cambiaría qué
# se confirma en Presupuestos Inteligentes, el comparador, similares y
# equivalencias, antes de medir el impacto real (Fase 3). La clasificación
# de abajo ya está decidida para cuando llegue esa activación -- no hay que
# rediseñar nada, solo unir estos dos conjuntos a los de arriba.
SPECS_COMPATIBILIDAD_NUEVAS = {
    "diametro_mm", "angulo_grados", "schedule", "amperaje_a",
    "calibre_awg", "energia_btu",
}
SPECS_RENDIMIENTO_NUEVAS = {"presion_psi", "potencia_hp"}
TODAS_LAS_SPECS_NUEVAS = SPECS_COMPATIBILIDAD_NUEVAS | SPECS_RENDIMIENTO_NUEVAS

# Acepta fracciones ("1/16 galon", "1/2 Hp") -- definida antes de _PATRONES
# porque potencia_hp (más abajo) también la usa, no solo _PATRONES_VOLUMEN.
_NUM_O_FRACCION = r'\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+(?:[.,]\d+)?'

# Auditoría real (ver EQUIVALENCIAS.md): "2-1/2 pulg" (número mixto con
# guion, muy común en este catálogo -- national hardware, stanley) se leía
# mal como solo "1/2" = 0.5, ignorando el "2-" -- el patrón original solo
# aceptaba espacio ("2 1/2"), no guion, entre el entero y la fracción.
_PATRONES = {
    # Bug real y ya activo en producción, encontrado en la Fase 2 de este
    # trabajo probando la spec nueva "schedule" contra el catálogo real:
    # sin el lookbehind, "Adaptador macho PVC SCH40 1/2"" leía "40 1/2"
    # como número mixto (40.5 pulg) -- el "40" de "SCH40" (una spec
    # completamente distinta) se colaba como si fuera la parte entera de
    # la fracción, con solo un espacio de por medio. Confirmado contra el
    # catálogo real: 55 de 280 productos SCH+fracción revisados daban un
    # diámetro > 10 pulg, físicamente imposible para tubería/conduit de
    # este catálogo. El lookbehind exige que el número no esté pegado (ni
    # por espacio ni directamente) a una letra o dígito de un token
    # anterior -- rechaza empezar el match a mitad de "sch40". Ver
    # MOTOR_ESPECIFICACIONES_ANALISIS.md.
    "diametro_pulg": re.compile(r'(?<![a-z0-9])(\d+[\s\-]+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+)\s*(?:"|″|”|pulg)'),
    "calibre": re.compile(r'#\s*(\d+)\b'),
    "diametro_mm": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*mm\b'),
    # A diferencia de longitud_m (SPECS_RENDIMIENTO, con tolerancia -- un
    # rollo de cable de 95m es un sustituto razonable de uno de 100m para
    # presupuestos.py), acá un tubo de 40cm nunca es sustituto de uno de
    # 100cm: es compatibilidad física, no rendimiento. Se guarda aparte,
    # sin tocar longitud_m ni su semántica ya usada en otro lado.
    # El segundo caso de la alternancia cubre "30x244 centimetros" (Novex):
    # ancho x largo seguido de la unidad al final, no pegada al primer
    # número -- sin esto, esa medida no se detectaba en absoluto. La "x"
    # exige estar pegada a los números (sin espacios) para no confundirse
    # con una medida en pulgadas separada por espacios como "1/2 x 60 cm"
    # (ahí "60" es la única medida real; "2 x 60 cm" nunca debe leerse
    # como si "2" fuera el valor).
    "longitud_cm": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*(?:cm\b|[xX]\d+(?:[.,]\d+)?\s*(?:cm|centimetros?)\b)'),
    "potencia_w": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*w\b'),
    # Ampliado para "220Vac"/"250 Vac"/"220Volt" -- el patrón original
    # (\bNv\b) exige que nada siga a la "v", así que nunca capturaba estas
    # variantes (Fase 1, MOTOR_ESPECIFICACIONES_ANALISIS.md: ~215 casos
    # reales). Es un superconjunto estricto del patrón anterior: todo lo
    # que ya capturaba sigue capturándolo igual.
    "voltaje": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*v(?:ac|olt)?\b'),
    "longitud_m": re.compile(r'(?<![a-z0-9])(\d+(?:[.,]\d+)?)\s*m\b(?!m)'),
    "peso_kg": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*kg\b'),
    "peso_lb": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*(?:lb|lbs|libras?)\b'),
    # "pzas"/"ud" agregados en Fase 2 -- mismo patrón, mismo significado que
    # "piezas"/"uds", solo faltaban en la enumeración (288 y 110 casos
    # reales sin cubrir, ver MOTOR_ESPECIFICACIONES_ANALISIS.md).
    "cantidad_unidades": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*(?:uds|pcs|unidades|piezas|pzas|ud)\b'),
    # --- Fase 2: specs nuevas (ver SPECS_COMPATIBILIDAD_NUEVAS/
    # SPECS_RENDIMIENTO_NUEVAS más arriba -- se extraen, no se comparan
    # todavía) ---
    # Símbolo real de grados (°) o el indicador ordinal masculino (º), que
    # el catálogo usa indistintamente para lo mismo (confirmado: "90º" y
    # "90°" aparecen ambos, mismo significado). Excluye explícitamente
    # cuando el símbolo va pegado a "C"/"F" -- ahí es temperatura
    # (Celsius/Fahrenheit), no ángulo (33 casos reales mezclados en el
    # mismo catálogo, ver Hallazgo del análisis). La palabra "grados" se
    # exige en plural a propósito: "grado" (singular, sin acento) es
    # ambiguo con grado de acero de varilla de construcción ("Varilla...
    # grado 60" = resistencia del acero, no un ángulo) -- "grados" en
    # plural nunca se usó así en el catálogo real revisado.
    "angulo_grados": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*(?:(?:°|º)(?![cf])|grados\b)'),
    # "SCH40"/"SCH 40"/"SCH80" -- cédula de tubería PVC, prefijo sin
    # ambigüedad conocida en el catálogo.
    "schedule": re.compile(r'\bsch\s*(\d+)\b'),
    # Calibre de cable eléctrico (AWG) -- clave separada de "calibre"
    # (que es #N: broca, varilla, cordel de albañil, lámina de gypsum,
    # ver Hallazgo #2 del análisis) porque son magnitudes físicas
    # distintas medidas en escalas distintas; mezclarlas sería inventar
    # una comparación que no tiene sentido físico.
    "calibre_awg": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*awg\b'),
    "presion_psi": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*psi\b'),
    # HP acepta fracciones ("1/2 Hp", "1/3 Hp", "3/4 Hp") -- extremadamente
    # común en motores/bombas reales del catálogo (motor de portón, bomba
    # periférica, triturador). Sin _NUM_O_FRACCION, "1/2 Hp" se leía como
    # "2" (el denominador solo), el mismo bug ya corregido para
    # diametro_pulg y volumen -- se evita desde el inicio acá. Clave
    # separada de potencia_w, sin convertir entre sí, mismo criterio que
    # peso_kg/peso_lb: HP y W tienden a usarse en categorías de producto
    # distintas (motores a gasolina vs. herramientas eléctricas), no es
    # la misma ambigüedad cruzada que sí justificó normalizar volumen_l.
    "potencia_hp": re.compile(rf'\b({_NUM_O_FRACCION})\s*hp\b'),
}

# Volumen: a diferencia de peso_kg/peso_lb (que se guardan separados, sin
# convertir entre sí), acá sí se normaliza todo a litros -- galón/litro/ml
# son, en la práctica, la forma en que CUALQUIER proveedor (no solo
# Pinturas, ver familias.py para ese caso ya cubierto) describe el
# volumen de líquidos (thinner, selladores, limpiadores, adhesivos
# líquidos), y sin normalizar, "1 galón" y "3.79 l" del mismo producto
# nunca se podrían comparar aunque sean literalmente la misma cantidad.
# Factor de conversión: galón estadounidense (3.785 L), el que usan de
# hecho los proveedores de este catálogo (Costa Rica).
#
# El grupo numérico acepta fracciones ("1/16 galon", "1/4 galon", ver
# _NUM_O_FRACCION más arriba) -- sin esto, "1/16 galon" se leía mal como
# "16" (el regex ignoraba el "1/" y capturaba solo el denominador), dando
# 16 galones donde el producto real es 1/16 de galón: un error de 256x que
# no bloqueaba nada porque parecía un valor válido. Bug real encontrado
# corriendo el motor de equivalencias completo (línea "Masilla Wood
# Filler" de Lanco, ver EQUIVALENCIAS.md).
_PATRONES_VOLUMEN = (
    (re.compile(rf'\b({_NUM_O_FRACCION})\s*(?:ml|mililitros?)\b'), 0.001),
    (re.compile(rf'\b({_NUM_O_FRACCION})\s*(?:l|lt|litros?)\b'), 1.0),
    (re.compile(rf'\b({_NUM_O_FRACCION})\s*(?:gal|gl|galon(?:es)?)\b'), 3.785),
)


def _extraer_volumen_l(texto):
    for patron, factor_a_litros in _PATRONES_VOLUMEN:
        coincidencia = patron.search(texto)
        if coincidencia:
            valor = _texto_a_numero(coincidencia.group(1))
            if valor is not None:
                return round(valor * factor_a_litros, 4)
    return None


TODAS_LAS_SPECS = SPECS_COMPATIBILIDAD | SPECS_UNIDAD_VENTA | SPECS_RENDIMIENTO


def _preparar(nombre):
    texto = (nombre or "").lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto


def _texto_a_numero(texto):
    texto = texto.strip()
    if "/" in texto:
        # "2-1/2" (guion, común en este catálogo) y "2 1/2" (espacio) son
        # la misma notación de número mixto -- normalizar el guion a
        # espacio antes de partir es lo único que hace falta para que el
        # resto de esta función (ya escrita para el caso con espacio)
        # funcione igual para ambos.
        partes = texto.replace("-", " ").split()
        entero = 0
        fraccion_texto = texto
        if len(partes) == 2:
            entero_texto, fraccion_texto = partes
            try:
                entero = float(entero_texto)
            except ValueError:
                entero = 0
        num, _, den = fraccion_texto.partition("/")
        try:
            return round(entero + float(num.strip()) / float(den.strip()), 4)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(texto.replace(",", "."))
    except ValueError:
        return None


# Amperaje pegado al número, sin espacio ("15A", "40A") -- deliberadamente
# NO se acepta "15 A" con espacio: se probó contra el catálogo real y esa
# forma captura de forma indistinguible tanto amperaje real ("Enchufe...
# 15 A") como el uso de "a" como preposición ("Conectores... #40 A
# Presión", "#8 A #10" como rango, "12 a 60 V" como rango) -- sin una señal
# léxica para separarlos, es más seguro perder algunos casos reales con
# espacio que inventar una incompatibilidad falsa entre productos sin
# relación. Ver MOTOR_ESPECIFICACIONES_ANALISIS.md, Hallazgo #3.
_PATRON_AMPERAJE = re.compile(r'\b(\d+(?:[.,]\d+)?)a\b')

# Tope de magnitud: 287 de 1,680 coincidencias reales de este patrón son
# en realidad códigos de catálogo de una sola línea de productos
# (lámparas, tomacorrientes, baterías) que por coincidencia terminan en un
# número seguido de "a" -- "21142A-8H-BK", "1009A-W", "2720A". Ningún
# producto eléctrico residencial/comercial de este catálogo llega a esa
# magnitud (el valor real más alto encontrado, 2000A, es de una pinza
# amperimétrica). No es un filtro perfecto -- un código corto que caiga
# por azar bajo este tope puede seguir colándose -- pero elimina la
# contaminación más grande y obvia sin excluir ningún amperaje plausible.
_AMPERAJE_MAXIMO_PLAUSIBLE = 5000


def _extraer_amperaje_a(texto):
    coincidencia = _PATRON_AMPERAJE.search(texto)
    if not coincidencia:
        return None
    valor = _texto_a_numero(coincidencia.group(1))
    if valor is None or valor <= 0 or valor >= _AMPERAJE_MAXIMO_PLAUSIBLE:
        return None
    return valor


_PATRON_BTU = re.compile(r'\b(\d{1,3}[.,]\d{3}|\d+)\s*btu\b')


def _extraer_energia_btu(texto):
    """BTU es la única spec de este archivo donde el catálogo usa "." o ","
    como separador de MILES en vez de decimal: "12.000 BTU" y "12,000 BTU"
    significan doce mil, no doce -- conviven en el mismo catálogo con la
    forma sin separador ("12000 BTU"). _texto_a_numero() no puede
    distinguir esto de un decimal real (que sí existe para otras specs,
    ej. "1.5 Hp") sin más contexto, así que se resuelve acá, específico de
    esta unidad: ningún equipo de A/C o calefacción del catálogo real
    tiene un valor de BTU fraccionario, así que un grupo de exactamente
    3 dígitos después del separador siempre es miles. Ver
    MOTOR_ESPECIFICACIONES_ANALISIS.md, Hallazgo #4."""

    coincidencia = _PATRON_BTU.search(texto)
    if not coincidencia:
        return None
    bruto = coincidencia.group(1)
    if re.fullmatch(r"\d{1,3}[.,]\d{3}", bruto):
        return float(re.sub(r"[.,]", "", bruto))
    return _texto_a_numero(bruto)


def extraer_specs(nombre):
    """Devuelve un dict {clave: valor_numerico} con las especificaciones
    detectadas en el nombre. Claves ausentes significan "no se pudo
    detectar", nunca "el producto no tiene esa especificación" -- son cosas
    distintas y el resto del sistema tiene que tratarlas distinto.

    Incluye las specs nuevas de la Fase 2 (SPECS_COMPATIBILIDAD_NUEVAS /
    SPECS_RENDIMIENTO_NUEVAS) -- se extraen siempre, pero comparar_specs()
    todavía no las compara (no están en TODAS_LAS_SPECS), así que agregar
    estas claves no cambia ningún veredicto existente."""

    texto = _preparar(nombre)
    specs = {}

    for clave, patron in _PATRONES.items():
        coincidencia = patron.search(texto)
        if coincidencia:
            valor = _texto_a_numero(coincidencia.group(1))
            if valor is not None:
                specs[clave] = valor

    volumen_l = _extraer_volumen_l(texto)
    if volumen_l is not None:
        specs["volumen_l"] = volumen_l

    amperaje = _extraer_amperaje_a(texto)
    if amperaje is not None:
        specs["amperaje_a"] = amperaje

    btu = _extraer_energia_btu(texto)
    if btu is not None:
        specs["energia_btu"] = btu

    return specs


# familias.analizar_nombre() (no se toca -- validado y ya usado por la
# agrupación de familias en producción) trata CUALQUIER número suelto del
# nombre como tamaño de empaque (1, 5, 55 galones...). Correcto para su
# uso original, pero un número de línea de producto ("Latex 3000") queda
# mezclado en la misma etiqueta ("3000 Cuarto" en vez de "Cuarto").
# Ningún tamaño de empaque real de este catálogo tiene 4+ dígitos, así que
# se descarta ese token de la etiqueta (no la etiqueta completa -- "3000
# Cuarto" y "3000 Galón" seguirían siendo presentaciones distintas una
# vez quitado el "3000" de ambas). Mismo hallazgo y misma corrección que
# ya existía en equivalencias.py para su propio uso; se aplica acá, en la
# fuente, para que unidad_comercial() y presupuestos.py también queden
# corregidos sin duplicar la lógica.
_PATRON_NUMERO_LINEA = re.compile(r"\b\d{4,}\b")


def extraer_presentacion_pintura(nombre):
    """Para Pinturas la 'presentación' (Galón/Cubeta/Cuarto) es una palabra,
    no un número -- los patrones de arriba no la detectan. Reutiliza
    familias.analizar_nombre(), ya existente y ya probado, en vez de
    reinventar esa lista de sinónimos."""

    _, _, presentacion = _analizar_presentacion_pintura(nombre or "")
    if not presentacion:
        return None
    limpia = _PATRON_NUMERO_LINEA.sub("", presentacion).strip()
    return limpia or None


# Área de cobertura (cerámica/porcelanato: "2.08m2", "2,08 m²") -- señal de
# unidad de venta, no de compatibilidad física, así que vive aparte de
# _PATRONES (esos alimentan comparar_specs(); esto solo alimenta
# unidad_comercial(), nunca debe participar de una decisión de
# equivalencia). Hallazgo real: PRUEBA_INGENIERO_BANO.md, "la interfaz no
# explica inequívocamente si la unidad es caja, pieza o paquete" -- el
# dato ya está en el nombre, solo no se mostraba de forma clara.
_PATRON_AREA_M2 = re.compile(r'\b(\d+(?:[.,]\d+)?)\s*m(?:2|²)\b')


def unidad_comercial(nombre, categoria=None):
    """Etiqueta legible de la unidad en que se vende el producto (Galón,
    25 kg, 2.08 m²...) para mostrarla en vez de un "c/u" ambiguo -- ver
    PRUEBA_INGENIERO_BANO.md, hallazgo #2. Nunca inventa un dato que no
    esté ya en el nombre: si no hay señal confiable, devuelve None (mejor
    no mostrar nada que mostrar una unidad incorrecta).

    Deliberadamente NO usa cantidad_unidades (especificaciones.py): esa
    señal es ambigua para mostrar como "unidad de venta" -- "Inodoro
    Malibú 2 piezas" trae cantidad_unidades=2, pero esas 2 piezas
    describen la construcción del inodoro (tanque + taza por separado),
    no que la compra trae dos inodoros. Mostrar "2 piezas" ahí sería
    inventar información engañosa, exactamente lo que este módulo evita."""

    if categoria == "Pinturas":
        presentacion = extraer_presentacion_pintura(nombre)
        if presentacion:
            return presentacion

    specs = extraer_specs(nombre)
    if "volumen_l" in specs:
        return f"{specs['volumen_l']:g} L"
    if "peso_kg" in specs:
        return f"{specs['peso_kg']:g} kg"
    if "peso_lb" in specs:
        return f"{specs['peso_lb']:g} lb"

    coincidencia_area = _PATRON_AREA_M2.search(_preparar(nombre or ""))
    if coincidencia_area:
        valor = _texto_a_numero(coincidencia_area.group(1))
        if valor is not None:
            return f"{valor:g} m²"

    return None


def comparar_specs(specs_a, specs_b):
    """Compara dos diccionarios de specs ya extraídos. Devuelve:
    - conflicto: True si hay una especificación de compatibilidad o de
      unidad de venta que ambos tienen y que difiere -- esto es un
      descalificador duro, sin importar qué tan parecidos sean los nombres.
    - coincidencias: claves donde ambos tienen valor y coinciden (evidencia
      a favor).
    - asimetrias: claves de unidad de venta donde solo uno de los dos tiene
      valor detectado -- no descalifica, pero tampoco se puede confirmar
      con esa evidencia ausente en un lado.
    """

    conflicto = False
    conflicto_en = []
    coincidencias = []
    asimetrias = []

    for clave in TODAS_LAS_SPECS:
        valor_a = specs_a.get(clave)
        valor_b = specs_b.get(clave)

        if valor_a is None and valor_b is None:
            continue

        if valor_a is None or valor_b is None:
            if clave in SPECS_UNIDAD_VENTA:
                asimetrias.append(clave)
            continue

        if clave in SPECS_RENDIMIENTO:
            base = max(valor_a, 1e-9)
            if abs(valor_a - valor_b) / base <= TOLERANCIA_RENDIMIENTO:
                coincidencias.append(clave)
            # Rendimiento nunca genera conflicto duro -- ver SPECS_RENDIMIENTO.
            continue

        # Compatibilidad y unidad de venta: coincidencia exacta o conflicto.
        if abs(valor_a - valor_b) < 1e-9:
            coincidencias.append(clave)
        else:
            conflicto = True
            conflicto_en.append(clave)

    return {
        "conflicto": conflicto,
        "conflicto_en": conflicto_en,
        "coincidencias": coincidencias,
        "asimetrias": asimetrias,
    }
