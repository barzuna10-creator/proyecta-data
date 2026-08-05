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
from .modelo import (
    CuadroAcabados,
    CuadroPuertas,
    CuadroVentanas,
    EntradaIndice,
    Lamina,
    Proyecto,
    TipoPdf,
)
from .nucleo import leer_proyecto

# Import por efecto secundario: registra cuadro_puertas/cuadro_ventanas/
# cuadro_acabados en el registro de extractores de lámina (ver
# LECTURA_DE_PLANOS_V2_CUADROS.md). nucleo.leer_proyecto() los corre de
# forma genérica sin conocer este módulo.
from . import cuadros  # noqa: F401,E402
from .cuadros import agregar_cuadros  # noqa: E402

__all__ = [
    "TipoPdf",
    "Lamina",
    "Proyecto",
    "EntradaIndice",
    "CuadroAcabados",
    "CuadroPuertas",
    "CuadroVentanas",
    "leer_proyecto",
    "agregar_cuadros",
    "buscar_lamina",
    "lamina_por_pagina",
    "laminas_por_disciplina",
    "laminas_sin_codigo",
    "laminas_sin_disciplina",
    "resumen",
]
