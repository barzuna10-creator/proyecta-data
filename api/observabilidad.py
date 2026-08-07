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

RELEASE_CANDIDATE.md (endurecimiento para los primeros clientes reales)
agrega acá un `request_id` por petición -- lo mínimo para poder
diagnosticar un problema puntual que reporte un cliente: buscar ese ID
en los logs encuentra la línea REQUEST y, si esa petición disparó un
análisis de plano o una generación de cotización automática, también
esas líneas (ver api/repositorio_proyectos.py) -- sin tener que adivinar
cuál de las miles de líneas del log corresponde al reporte. Se guarda en
un contextvar (no un parámetro que haya que pasar a mano por cada
función) porque analizar_plano()/generar_cotizacion_automatica() viven
en repositorio_proyectos.py, una capa que a propósito no importa nada de
FastAPI ni conoce el `Request` -- el contextvar es la única forma de que
ese código, varios niveles más abajo en la misma petición, pueda etiquetar
sus propias líneas con el mismo ID sin acoplar esa capa al framework
web."""

import contextvars
import logging
import time
import uuid
from pathlib import Path

from fastapi import Request

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

logger = logging.getLogger("proyecta_api")
logger.setLevel(logging.INFO)

_id_peticion_actual = contextvars.ContextVar("id_peticion_actual", default="-")


def id_de_peticion_actual():
    """El request_id de la petición HTTP en curso (ver middleware_logging
    más abajo) -- "-" fuera de una petición real (ej. un script CLI que
    reutiliza estas funciones de repositorio_proyectos.py directamente,
    sin pasar por la API)."""
    return _id_peticion_actual.get()


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


def _id_entrante_o_nuevo(request: Request):
    # Si ya llega un X-Request-Id (un proxy/balanceador delante de Render,
    # o un reintento del mismo cliente correlacionando su propio intento),
    # se respeta -- generar uno nuevo igual serviría para diagnosticar,
    # pero perdería la correlación con lo que el cliente/infra de borde ya
    # etiquetó. hex[:12] alcanza de sobra para grep en un solo proceso;
    # no hace falta un UUID completo en cada línea de log.
    entrante = request.headers.get("x-request-id")
    return entrante if entrante else uuid.uuid4().hex[:12]


async def middleware_logging(request: Request, call_next):
    _asegurar_handler()
    id_peticion = _id_entrante_o_nuevo(request)
    token = _id_peticion_actual.set(id_peticion)
    inicio = time.perf_counter()
    try:
        try:
            respuesta = await call_next(request)
        except Exception:
            duracion_ms = round((time.perf_counter() - inicio) * 1000, 1)
            logger.exception(
                f"REQUEST id={id_peticion} metodo={request.method} ruta={request.url.path} "
                f"estado=500 duracion_ms={duracion_ms} usuario={_usuario_desde_encabezado(request)} "
                "-- excepción no manejada"
            )
            raise
    finally:
        _id_peticion_actual.reset(token)

    duracion_ms = round((time.perf_counter() - inicio) * 1000, 1)
    logger.info(
        f"REQUEST id={id_peticion} metodo={request.method} ruta={request.url.path} "
        f"estado={respuesta.status_code} duracion_ms={duracion_ms} "
        f"usuario={_usuario_desde_encabezado(request)}"
    )
    # Devuelto al cliente -- si un cliente piloto reporta "se quedó
    # pegado" o "me tiró un error", este header (visible en cualquier
    # inspector de red del navegador) es lo que hay que pedirle para
    # encontrar la línea exacta en los logs, en vez de adivinar por
    # hora aproximada.
    respuesta.headers["X-Request-Id"] = id_peticion
    return respuesta
