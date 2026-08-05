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
