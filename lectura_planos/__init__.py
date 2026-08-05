"""LECTURA_DE_PLANOS_V1_MVP -- ver LECTURA_DE_PLANOS_V1_MVP.md.

Uso mínimo:

    import lectura_planos as lp
    proyecto = lp.leer_proyecto("mi_plano.pdf")
    lp.resumen(proyecto)
"""

from .api import (
    buscar_lamina,
    lamina_por_pagina,
    laminas_por_disciplina,
    laminas_sin_codigo,
    laminas_sin_disciplina,
    resumen,
)
from .modelo import EntradaIndice, Lamina, Proyecto, TipoPdf
from .nucleo import leer_proyecto

__all__ = [
    "TipoPdf",
    "Lamina",
    "Proyecto",
    "EntradaIndice",
    "leer_proyecto",
    "buscar_lamina",
    "lamina_por_pagina",
    "laminas_por_disciplina",
    "laminas_sin_codigo",
    "laminas_sin_disciplina",
    "resumen",
]
