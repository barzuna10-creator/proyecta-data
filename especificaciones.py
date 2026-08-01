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
SPECS_COMPATIBILIDAD = {"diametro_pulg", "calibre"}

# Specs de "unidad de venta": si ambos las tienen y difieren, tampoco son
# comparables (500 uds. no es lo mismo que 1000 uds.) -- pero si solo UNO
# de los dos productos tiene un valor detectado (asimetría), no se puede
# confirmar ni descartar con certeza, así que nunca alcanza el nivel más
# alto de confianza solo por esto (ver presupuestos.py).
SPECS_UNIDAD_VENTA = {"peso_kg", "peso_lb", "cantidad_unidades"}

# Specs de rendimiento: se comparan con tolerancia, nunca descalifican por
# sí solas -- dos taladros de 750W y 710W siguen siendo sustitutos
# razonables.
SPECS_RENDIMIENTO = {"potencia_w", "voltaje", "longitud_m"}

TOLERANCIA_RENDIMIENTO = 0.20  # 20%, igual que el peso_similar de similares.py

_PATRONES = {
    "diametro_pulg": re.compile(r'(\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+)\s*(?:"|″|pulg)'),
    "calibre": re.compile(r'#\s*(\d+)\b'),
    "diametro_mm": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*mm\b'),
    "potencia_w": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*w\b'),
    "voltaje": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*v\b'),
    "longitud_m": re.compile(r'(?<![a-z0-9])(\d+(?:[.,]\d+)?)\s*m\b(?!m)'),
    "peso_kg": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*kg\b'),
    "peso_lb": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*(?:lb|lbs|libras?)\b'),
    "cantidad_unidades": re.compile(r'\b(\d+(?:[.,]\d+)?)\s*(?:uds|pcs|unidades)\b'),
}

TODAS_LAS_SPECS = SPECS_COMPATIBILIDAD | SPECS_UNIDAD_VENTA | SPECS_RENDIMIENTO


def _preparar(nombre):
    texto = (nombre or "").lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto


def _texto_a_numero(texto):
    texto = texto.strip()
    if "/" in texto:
        partes = texto.split()
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


def extraer_specs(nombre):
    """Devuelve un dict {clave: valor_numerico} con las especificaciones
    detectadas en el nombre. Claves ausentes significan "no se pudo
    detectar", nunca "el producto no tiene esa especificación" -- son cosas
    distintas y el resto del sistema tiene que tratarlas distinto."""

    texto = _preparar(nombre)
    specs = {}

    for clave, patron in _PATRONES.items():
        coincidencia = patron.search(texto)
        if coincidencia:
            valor = _texto_a_numero(coincidencia.group(1))
            if valor is not None:
                specs[clave] = valor

    return specs


def extraer_presentacion_pintura(nombre):
    """Para Pinturas la 'presentación' (Galón/Cubeta/Cuarto) es una palabra,
    no un número -- los patrones de arriba no la detectan. Reutiliza
    familias.analizar_nombre(), ya existente y ya probado, en vez de
    reinventar esa lista de sinónimos."""

    _, _, presentacion = _analizar_presentacion_pintura(nombre or "")
    return presentacion or None


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
