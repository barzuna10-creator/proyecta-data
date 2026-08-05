"""Modelo de datos de LECTURA_DE_PLANOS_V1_MVP (ver LECTURA_DE_PLANOS_V1_MVP.md
y, para el diseño completo de largo plazo, LECTURA_DE_PLANOS_V1_ARQUITECTURA.md).

Deliberadamente plano y sin comportamiento propio -- clasificacion.py,
extractores.py y nucleo.py construyen y llenan estas estructuras, nunca al
revés. Así un extractor futuro (acabados, puertas, ventanas, áreas) solo
necesita producir datos que encajen en Lamina.extras/Proyecto.extras, sin
que este modelo tenga que cambiar de forma para acomodarlo.
"""

from dataclasses import dataclass, field
from enum import Enum


class TipoPdf(str, Enum):
    """Clasificación de la etapa [0] (ver LECTURA_DE_PLANOS_V1_ARQUITECTURA.md, sección 2).

    Solo VECTORIAL_CON_TEXTO está validado contra planos reales (ver
    limitaciones en LECTURA_DE_PLANOS_V1_MVP.md) -- las otras tres son
    heurísticas razonadas, no medidas.
    """

    VECTORIAL_CON_TEXTO = "vectorial_con_texto"
    VECTORIAL_SIN_TEXTO = "vectorial_sin_texto"
    HIBRIDO = "hibrido"
    ESCANEADO = "escaneado"


@dataclass(frozen=True)
class EntradaIndice:
    """Una fila del índice de planos, cuando el PDF trae uno."""

    codigo: str
    nombre: str


@dataclass
class Lamina:
    numero_pagina: int  # 1-based, coincide con el número de página real del PDF
    tipo_pdf: TipoPdf
    codigo: str | None  # ej. "A002", "S104" -- None si no se pudo leer, nunca inventado
    nombre: str | None  # ej. "PLANTA DE CONJUNTO N 0.0 M" -- None si no se pudo leer
    disciplina: str | None  # ver clasificacion.clasificar_disciplina -- None = sin determinar
    cajetin: dict = field(default_factory=dict)  # campos crudos leídos del cajetín de esta hoja
    extras: dict = field(default_factory=dict)  # resultados de extractores de lámina futuros, por nombre


@dataclass
class Proyecto:
    ruta_pdf: str
    nombre: str  # del cajetín/portada si se encontró; si no, el nombre del archivo (ver advertencias)
    cantidad_laminas: int
    disciplina: str  # única disciplina si todas las láminas coinciden, "mixto" si no, "sin_determinar" si ninguna
    laminas: list[Lamina]
    indice: list[EntradaIndice] | None  # None = no se encontró un índice de planos en el PDF
    advertencias: list[str] = field(default_factory=list)  # todo lo que no se pudo determinar, nunca silenciado
    extras: dict = field(default_factory=dict)  # resultados de extractores de documento futuros, por nombre


# ---------------------------------------------------------------------------
# LECTURA_DE_PLANOS_V2_CUADROS -- ver LECTURA_DE_PLANOS_V2_CUADROS.md.
# Tres tipos de cuadro, cada uno con su propio campo `confianza` ("alta" |
# "media" | "baja") y `texto_original` (evidencia cruda, sin segmentar, de
# la fila completa -- nunca se descarta aunque la segmentación en campos
# falle o sea parcial). `cantidad` en puertas/ventanas queda casi siempre
# None: ningún cuadro real auditado trae una columna de cantidad -- eso
# requeriría contar símbolos en planta, explícitamente fuera de alcance.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CuadroAcabados:
    codigo: str
    descripcion: str | None
    marca: str | None
    formato: str | None
    color: str | None
    ubicacion: str  # "muros_y_paredes" | "pisos" | "cielos" -- de qué sub-cuadro salió
    pagina_fuente: int
    texto_original: str
    confianza: str


@dataclass(frozen=True)
class CuadroPuertas:
    codigo: str
    ancho: float | None
    alto: float | None
    tipo: str | None
    material: str | None
    cantidad: int | None
    pagina_fuente: int
    texto_original: str
    confianza: str


@dataclass(frozen=True)
class CuadroVentanas:
    codigo: str
    ancho: float | None
    alto: float | None
    tipo: str | None
    material: str | None
    cantidad: int | None
    pagina_fuente: int
    texto_original: str
    confianza: str


# ---------------------------------------------------------------------------
# LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO -- ver
# LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO.md. Deliberadamente NO incluye
# acabados-por-espacio ni puertas/ventanas-asociadas-a-espacio: la
# auditoría de esa fase midió que esas relaciones no tienen evidencia
# textual inequívoca en los planos reales (requerirían distancia
# geométrica o contención de polígono para resolver la ambigüedad,
# ambas explícitamente excluidas). Solo se modela lo que sí es una
# relación explícita y sin ambigüedad: niveles, catálogo de espacios por
# nivel, y referencias entre láminas (callouts de sección/detalle, que ya
# traen su propio destino en el mismo bloque de texto).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Nivel:
    nombre: str  # tal como aparece en los nombres de lámina, ej. "N 0.0 M"
    elevacion: float | None  # valor numérico parseado, ej. 0.0, -3.5 -- None si el formato no fue el esperado
    laminas: tuple  # códigos de lámina que declaran pertenecer a este nivel


@dataclass(frozen=True)
class Espacio:
    nombre: str  # ej. "COCINA", tal como aparece en la lámina de distribución
    nivel: str | None  # nombre del Nivel al que pertenece -- None si no se pudo determinar
    pagina_fuente: int
    texto_original: str


@dataclass(frozen=True)
class ReferenciaLamina:
    tipo: str  # "seccion" | "detalle"
    identificador: str  # letra de sección (ej. "A") o número de detalle (ej. "1")
    lamina_origen: str | None  # código de la lámina donde aparece el callout
    lamina_destino: str  # código de la lámina referenciada
    pagina_fuente: int
    destino_existe: bool  # True si lamina_destino aparece en el índice de este documento


@dataclass
class ModeloEdificio:
    proyecto_nombre: str
    niveles: list
    espacios: list
    referencias_laminas: list
    advertencias: list = field(default_factory=list)
