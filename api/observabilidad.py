"""Logging estructurado + analítica mínima (ver BETA_1.0_CHECKLIST.md,
hallazgos 1.1 y 8.1: "cero registro de errores del backend" / "ninguna
forma de saber si los usuarios están usando Proyecta").

Un solo mecanismo resuelve los dos hallazgos a propósito, en vez de dos
sistemas separados: cada request ya deja un renglón con
método/ruta/estado/duración/usuario -- de ahí sale tanto la tasa de
error real (mirar `estado>=500`) como el uso real (contar usuarios
distintos, rutas más visitadas, por día). Construir una segunda
tubería de analítica aparte habría sido agregar algo que este único
registro ya cubre -- justo lo que la misión de este sprint pide no
hacer.

Mismo patrón que capa_intencion.py (logger con FileHandler propio,
carpeta logs/ ya en .gitignore) -- nada nuevo que aprender en este
código base, solo el mismo idioma aplicado a la API completa en vez de
a un solo módulo experimental.
"""

import logging
import time
from pathlib import Path

from fastapi import Request

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

logger = logging.getLogger("proyecta_api")
logger.setLevel(logging.INFO)


def _asegurar_handler():
    if logger.handlers:
        return
    _LOG_DIR.mkdir(exist_ok=True)
    handler = logging.FileHandler(_LOG_DIR / "proyecta_api.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    logger.addHandler(handler)
    # También a consola -- en Render (y en cualquier plataforma tipo PaaS)
    # los logs de stdout ya se capturan solos, sin depender de que el
    # disco donde vive logs/ persista entre despliegues.
    consola = logging.StreamHandler()
    consola.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    logger.addHandler(consola)
    logger.propagate = False


def _usuario_desde_encabezado(request: Request) -> str:
    """Best-effort, nunca lanza: para el log alcanza con saber si hubo
    sesión o no, no hace falta resolverla contra la base en cada
    request solo para loguear (eso ya lo hace obtener_propietario_id
    cuando el endpoint la necesita de verdad)."""
    auth = request.headers.get("authorization", "")
    return "con-sesion" if auth.startswith("Bearer ") else "anonimo"


async def middleware_logging(request: Request, call_next):
    _asegurar_handler()
    inicio = time.perf_counter()
    try:
        respuesta = await call_next(request)
    except Exception:
        duracion_ms = round((time.perf_counter() - inicio) * 1000, 1)
        logger.exception(
            f"REQUEST metodo={request.method} ruta={request.url.path} "
            f"estado=500 duracion_ms={duracion_ms} usuario={_usuario_desde_encabezado(request)} "
            "-- excepción no manejada"
        )
        raise
    duracion_ms = round((time.perf_counter() - inicio) * 1000, 1)
    logger.info(
        f"REQUEST metodo={request.method} ruta={request.url.path} "
        f"estado={respuesta.status_code} duracion_ms={duracion_ms} "
        f"usuario={_usuario_desde_encabezado(request)}"
    )
    return respuesta
