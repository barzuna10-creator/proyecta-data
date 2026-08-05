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
    PiezaEstructural,
    Proyecto,
    ReferenciaLamina,
    TipoPdf,
)
from .nucleo import leer_proyecto

# Import por efecto secundario: registra cuadro_puertas/cuadro_ventanas/
# cuadro_acabados (LECTURA_DE_PLANOS_V2_CUADROS.md), espacios/
# referencias_laminas (LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO.md) y
# computo_estructural (EXTRACTOR_COMPUTO_ESTRUCTURAL_V1.md) en el
# registro de extractores de lámina. nucleo.leer_proyecto() los corre de
# forma genérica sin conocer estos módulos.
from . import computo_estructural  # noqa: F401,E402
from . import cuadros  # noqa: F401,E402
from . import modelo_edificio  # noqa: F401,E402
from .computo_estructural import agregar_computo_estructural  # noqa: E402
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
    "PiezaEstructural",
    "leer_proyecto",
    "agregar_cuadros",
    "construir_modelo_edificio",
    "agregar_computo_estructural",
    "buscar_lamina",
    "lamina_por_pagina",
    "laminas_por_disciplina",
    "laminas_sin_codigo",
    "laminas_sin_disciplina",
    "resumen",
]
