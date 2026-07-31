"""Capa de intención (experimento controlado).

Detecta si una consulta activa alguno de los conceptos definidos en
conceptos_intencion.py y, si es así, devuelve qué filtro de categoría /
exclusión aplicar y qué palabras de contexto ignorar como requisito de
coincidencia.

Este módulo depende de busqueda.py (para normalizar y tokenizar igual que el
resto del buscador) -- pero busqueda.py NO depende de este módulo. El motor
de búsqueda no sabe que existen "conceptos"; solo expone parámetros
genéricos de filtro que esta capa decide cuándo usar. Así, si mañana se
quita esta capa por completo, el motor queda intacto.

Si ninguna consulta activa un concepto, esta capa no hace nada -- ver
USE_INTENT_LAYER en api/main.py para apagar la capa completa sin tocar el
motor de búsqueda.
"""

import logging
from pathlib import Path

from busqueda import normalizar_texto, tokenizar
from conceptos_intencion import CONCEPTOS, PALABRAS_CONTEXTO

_LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("capa_intencion")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.FileHandler(_LOG_DIR / "intencion.log", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False


def _normalizar_conjunto(palabras):
    return frozenset(normalizar_texto(p) for p in palabras)


PALABRAS_CONTEXTO_NORMALIZADAS = _normalizar_conjunto(PALABRAS_CONTEXTO)


def detectar_concepto(consulta):
    """Devuelve {"nombre", "categorias_permitidas", "exclusiones"} si la
    consulta activa un concepto, o None si no activa ninguno. Registra en
    logs/intencion.log cada vez que un concepto se activa."""

    tokens = set(tokenizar(normalizar_texto(consulta)))

    for definicion in CONCEPTOS:
        disparadores = _normalizar_conjunto(definicion["disparadores"])
        if not (tokens & disparadores):
            continue

        contexto_requerido = _normalizar_conjunto(definicion["contexto_requerido"])
        if contexto_requerido and not (tokens & contexto_requerido):
            continue

        nombre = definicion["nombre"]
        logger.info("consulta=%r concepto=%r", consulta, nombre)

        return {
            "nombre": nombre,
            "categorias_permitidas": definicion["categorias_permitidas"],
            "exclusiones": definicion["exclusiones"],
        }

    return None
