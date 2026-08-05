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
    Espacio,
    Lamina,
    ModeloEdificio,
    Nivel,
    Proyecto,
    ReferenciaLamina,
    TipoPdf,
)
from .nucleo import leer_proyecto

# Import por efecto secundario: registra cuadro_puertas/cuadro_ventanas/
# cuadro_acabados (LECTURA_DE_PLANOS_V2_CUADROS.md) y espacios/
# referencias_laminas (LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO.md) en el
# registro de extractores de lámina. nucleo.leer_proyecto() los corre de
# forma genérica sin conocer estos módulos.
from . import cuadros  # noqa: F401,E402
from . import modelo_edificio  # noqa: F401,E402
from .cuadros import agregar_cuadros  # noqa: E402
from .modelo_edificio import construir_modelo_edificio  # noqa: E402

__all__ = [
    "TipoPdf",
    "Lamina",
    "Proyecto",
    "EntradaIndice",
    "CuadroAcabados",
    "CuadroPuertas",
    "CuadroVentanas",
    "Nivel",
    "Espacio",
    "ReferenciaLamina",
    "ModeloEdificio",
    "leer_proyecto",
    "agregar_cuadros",
    "construir_modelo_edificio",
    "buscar_lamina",
    "lamina_por_pagina",
    "laminas_por_disciplina",
    "laminas_sin_codigo",
    "laminas_sin_disciplina",
    "resumen",
]
