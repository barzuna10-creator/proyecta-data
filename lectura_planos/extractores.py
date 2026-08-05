"""Registro de extractores sobre un PDF de planos, y los dos extractores del
MVP (índice de planos, cajetín).

El contrato es angosto a propósito: un extractor recibe el documento ya
abierto y clasificado (ContextoLectura) y devuelve un ResultadoExtractor
con su propio namespace de datos -- nunca vuelve a abrir el PDF ni a
reclasificar nada. Fases futuras (acabados, puertas, ventanas, áreas) se
agregan escribiendo una función nueva y registrándola con
@registrar_documento(...) o @registrar_lamina(...) -- nucleo.py recorre
ambos registros de forma genérica, así que agregar un extractor nunca
requiere tocar nucleo.py (ver LECTURA_DE_PLANOS_V1_MVP.md, sección de
diseño).

Índice y cajetín son extractores "de este MVP", no "futuros" -- por eso
nucleo.py sí conoce sus nombres para poblar los campos canónicos del
modelo (código, nombre, disciplina). Un extractor agregado más adelante
que NO deba alimentar esos campos canónicos no necesita ningún cambio en
nucleo.py: sus resultados caen solos en Lamina.extras/Proyecto.extras.
"""

import re
from dataclasses import dataclass, field
from typing import Callable

import fitz
import pdfplumber


@dataclass
class ContextoLectura:
    documento: object  # fitz.Document
    ruta_pdf: str
    tipos_por_pagina: list


@dataclass
class ResultadoExtractor:
    nombre: str
    datos: dict
    advertencias: list = field(default_factory=list)


ExtractorDocumento = Callable[[ContextoLectura], ResultadoExtractor]
ExtractorLamina = Callable[[ContextoLectura, int], ResultadoExtractor]

_REGISTRO_DOCUMENTO: dict[str, ExtractorDocumento] = {}
_REGISTRO_LAMINA: dict[str, ExtractorLamina] = {}


def registrar_documento(nombre: str):
    """Registra un extractor que corre una sola vez sobre todo el documento."""

    def decorador(funcion: ExtractorDocumento) -> ExtractorDocumento:
        _REGISTRO_DOCUMENTO[nombre] = funcion
        return funcion

    return decorador


def registrar_lamina(nombre: str):
    """Registra un extractor que corre una vez por cada página del documento."""

    def decorador(funcion: ExtractorLamina) -> ExtractorLamina:
        _REGISTRO_LAMINA[nombre] = funcion
        return funcion

    return decorador


def extractores_documento_registrados() -> dict:
    return dict(_REGISTRO_DOCUMENTO)


def extractores_lamina_registrados() -> dict:
    return dict(_REGISTRO_LAMINA)


# ---------------------------------------------------------------------------
# Índice de planos (etapa [1] parcial de LECTURA_DE_PLANOS_V1_ARQUITECTURA.md)
# ---------------------------------------------------------------------------

# Código de lámina real observado en los dos planos de la auditoría: 1-3
# letras seguidas de 2-4 dígitos, con una letra opcional al final (ej. "A002",
# "S104", "S401"). No es un estándar de la industria, es lo que se comprobó
# en estos dos juegos -- ver LECTURA_DE_PLANOS_V1_MVP.md.
PATRON_CODIGO_LAMINA = re.compile(r"^[A-Z]{1,3}\d{2,4}[A-Z]?$")

PALABRAS_INDICE = ("INDICE DE PLANOS", "ÍNDICE DE PLANOS", "INDICE DE LAMINAS", "ÍNDICE DE LÁMINAS")
PAGINAS_MAX_BUSQUEDA_INDICE = 3  # el índice, cuando existe, está entre las primeras hojas (confirmado)
UMBRAL_COINCIDENCIA_TABLA = 0.5  # al menos la mitad de las filas deben parecer {código, nombre} para aceptar la tabla


def _pagina_es_indice(texto: str) -> bool:
    texto_norm = texto.upper()
    return any(palabra in texto_norm for palabra in PALABRAS_INDICE)


def _tabla_parece_indice(tabla: list) -> float:
    if not tabla:
        return 0.0
    filas_validas = sum(
        1
        for fila in tabla
        if len(fila) >= 2 and fila[0] and PATRON_CODIGO_LAMINA.match(str(fila[0]).strip())
    )
    return filas_validas / len(tabla)


@registrar_documento("indice")
def extraer_indice(contexto: ContextoLectura) -> ResultadoExtractor:
    """Busca una hoja de índice entre las primeras páginas y, si la
    encuentra, extrae su tabla -- eligiendo entre TODAS las tablas que
    pdfplumber detecta en esa hoja la que más parece un índice real
    (columna de código con el patrón esperado), en vez de asumir que es
    "la primera tabla". Esto es una extracción DIRIGIDA a una hoja ya
    identificada por texto, no un lector de tablas genérico corriendo a
    ciegas sobre cualquier hoja."""
    documento = contexto.documento
    advertencias = []

    pagina_indice = None
    for numero in range(min(PAGINAS_MAX_BUSQUEDA_INDICE, documento.page_count)):
        if _pagina_es_indice(documento[numero].get_text("text")):
            pagina_indice = numero
            break

    if pagina_indice is None:
        return ResultadoExtractor("indice", {"entradas": None, "pagina": None})

    with pdfplumber.open(contexto.ruta_pdf) as pdf:
        tablas = pdf.pages[pagina_indice].extract_tables()

    mejor_tabla, mejor_puntaje = None, 0.0
    for tabla in tablas:
        puntaje = _tabla_parece_indice(tabla)
        if puntaje > mejor_puntaje:
            mejor_tabla, mejor_puntaje = tabla, puntaje

    if mejor_tabla is None or mejor_puntaje < UMBRAL_COINCIDENCIA_TABLA:
        advertencias.append(
            f"se encontró una hoja de índice (página {pagina_indice + 1}) pero ninguna tabla "
            "extraída se pudo confirmar como el índice real -- se devuelve vacío en vez de adivinar."
        )
        return ResultadoExtractor("indice", {"entradas": None, "pagina": pagina_indice + 1}, advertencias)

    from .modelo import EntradaIndice

    entradas = [
        EntradaIndice(codigo=str(fila[0]).strip(), nombre=(str(fila[1]).strip() if fila[1] else ""))
        for fila in mejor_tabla
        if len(fila) >= 2 and fila[0] and PATRON_CODIGO_LAMINA.match(str(fila[0]).strip())
    ]
    return ResultadoExtractor("indice", {"entradas": entradas, "pagina": pagina_indice + 1}, advertencias)


# ---------------------------------------------------------------------------
# Cajetín de cada lámina (etapa [2] parcial de LECTURA_DE_PLANOS_V1_ARQUITECTURA.md)
# ---------------------------------------------------------------------------

# Región inferior derecha, como fracción del rectángulo de página --
# confirmada empíricamente contra los dos planos reales (ver auditoría en
# LECTURA_DE_PLANOS_V1_MVP.md). No hay evidencia de que esta posición sea
# universal entre firmas/software distintos.
REGION_CAJETIN = (0.72, 0.80, 1.0, 1.0)

PATRON_FECHA = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
PATRON_ESCALA = re.compile(r"ESCALA\s*:?\s*1\s*:\s*\d+", re.IGNORECASE)
PATRON_SOLO_DATO_NUMERICO = re.compile(r"^[\d\-/:\s]+$")  # cédulas, folios, fechas: solo dígitos/guiones/etc.


def _extraer_codigo_y_nombre(lineas: list) -> tuple:
    """El código de lámina y su nombre aparecen, en los dos planos
    auditados, como líneas cortas cerca del final del bloque de cajetín:
    primero el código (coincide con PATRON_CODIGO_LAMINA), después --
    salteando líneas que son puramente numéricas (cédulas, folios) -- el
    nombre de la lámina. Confirmado línea por línea contra ambos PDFs
    reales, ver LECTURA_DE_PLANOS_V1_MVP.md."""
    indices_codigo = [i for i, linea in enumerate(lineas) if PATRON_CODIGO_LAMINA.match(linea)]
    if not indices_codigo:
        return None, None

    i = indices_codigo[-1]
    codigo = lineas[i]

    # Umbral mínimo de longitud: descarta ruido de una sola letra que a
    # veces cae en la región del cajetín cuando una hoja tiene una tabla
    # de despiece pegada al margen (observado en el plano de taller,
    # ver LECTURA_DE_PLANOS_V1_MVP.md) -- un nombre de lámina real
    # siempre es varias palabras.
    LONGITUD_MINIMA_NOMBRE = 4

    nombre = None
    for candidata in lineas[i + 1 :]:
        if PATRON_SOLO_DATO_NUMERICO.match(candidata) or len(candidata.strip()) < LONGITUD_MINIMA_NOMBRE:
            continue
        nombre = candidata
        break

    return codigo, nombre


@registrar_lamina("cajetin")
def extraer_cajetin(contexto: ContextoLectura, numero_pagina: int) -> ResultadoExtractor:
    pagina = contexto.documento[numero_pagina]
    r = pagina.rect
    x0, y0, x1, y1 = REGION_CAJETIN
    clip = fitz.Rect(r.width * x0, r.height * y0, r.width * x1, r.height * y1)
    texto = pagina.get_text("text", clip=clip).strip()

    advertencias = []
    datos = {"texto_crudo": texto}

    if not texto:
        advertencias.append("región del cajetín vacía en esta hoja -- no hay campos que leer.")
        return ResultadoExtractor("cajetin", datos, advertencias)

    match_fecha = PATRON_FECHA.search(texto)
    if match_fecha:
        datos["fecha"] = match_fecha.group(0)

    match_escala = PATRON_ESCALA.search(texto)
    if match_escala:
        datos["escala"] = match_escala.group(0)

    codigo, nombre = _extraer_codigo_y_nombre(texto.splitlines())
    if codigo:
        datos["codigo"] = codigo
    else:
        advertencias.append("no se pudo identificar un código de lámina en el cajetín de esta hoja.")
    if nombre:
        datos["nombre"] = nombre

    return ResultadoExtractor("cajetin", datos, advertencias)
