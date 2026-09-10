"""M9 -- Read-Only Business Context Expansion, Layer 1: a new, read-only,
pre-aggregated HTTP surface for Jarvis's business-context queries. A
namespace fully separate from /admin/metricas/* (api/routers/metricas.py,
which serves human admins over a session cookie via
`Depends(requerir_admin)`): every endpoint here is authenticated by a
dedicated service token, `BUSINESS_CONTEXT_TOKEN` -- never the human admin
session, and `requerir_admin`/`ADMIN_USUARIO_IDS` are never reused or
consulted here.

Every endpoint in this module:
- is GET only -- no endpoint here can write anything.
- returns ONLY pre-aggregated data (counts, percentages, top-N by category
  or by SKU) -- never a raw row of `proyectos`/`items_proyecto`/`eventos`.
- groups by `categoria` (already populated on every event with
  `origen='plano' AND confianza_match IS NOT NULL`, confirmed real in
  eventos.py) or by supplier/catalog identifiers -- NEVER by the raw
  `texto_material` column. `eventos.materiales_mas_dificiles()` groups by
  that raw column and is therefore never called from this module; only
  the shared ranking logic (`eventos._desempeno_agrupado`) is reused,
  always with `columna="categoria"` (M9 design, Correction 1 of V2). A
  "difficult material" that never got a normalized category is exposed
  only as a single generic "material no catalogado" bucket, with its
  count only -- never the original free-text.
- never touches `usuarios` in any way -- this module opens no connection
  that could reach that table (the only table it ever queries is
  `eventos`/`productos`, via `db.conectar()`, same connection helper the
  rest of `api/` already uses).
- returns two distinct timestamps, never one ambiguous one (M9 Correction
  2 of V2): `data_as_of` (computed from `MAX(fecha_creacion)` of the
  `eventos` actually considered, or `MAX(fecha_actualizacion)` of
  `productos` -- `None` with an explicit `data_as_of_reason` when no
  underlying date field applies, never invented) and `computed_at` (the
  real wall-clock time of this calculation).
- rate limits to 2 requests/minute PER TOKEN, aggregated across every
  business-context endpoint together (M9 Correction 9 of V3) -- a simple
  in-process token-bucket, no new dependency, sized against
  render.yaml's `--workers 1` (confirmed real -- see that file).
- truncates any top-N list to 10 entries (with an explicit `truncated`
  flag) and any string value to 500 characters (with a visible suffix).
- on an empty aggregate (e.g. `eventos` with zero matching rows), answers
  honestly with `evidence_status: "sin_evidencia_suficiente"` -- never a
  fabricated value like a manufactured "0% de fallo"."""

from __future__ import annotations

import hmac
import os
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException

import eventos
from db import conectar

router = APIRouter(prefix="/business-context/v1", tags=["business-context"])

MAX_TOP_N = 10
MAX_STRING_CHARS = 500
TRUNCATION_SUFFIX = "... [truncado]"
RATE_LIMIT_PER_MINUTE = 2
RATE_LIMIT_WINDOW_SECONDS = 60.0

MATERIAL_NO_CATALOGADO_LABEL = "material no catalogado"
EVIDENCE_SUFICIENTE = "ok"
EVIDENCE_INSUFICIENTE = "sin_evidencia_suficiente"


def truncar_texto(valor):
    """Trunca a MAX_STRING_CHARS con sufijo visible -- nunca silenciosamente."""
    if not isinstance(valor, str) or len(valor) <= MAX_STRING_CHARS:
        return valor
    return valor[:MAX_STRING_CHARS] + TRUNCATION_SUFFIX


class LimitadorDeTasa:
    """Token-bucket simple, en memoria del proceso, sin dependencia
    nueva. Cuenta agregada A TRAVÉS de todas las queries de negocio para
    un mismo token de servicio -- nunca por query individual (M9
    Corrección 9 de V3)."""

    def __init__(self, limite_por_minuto: int = RATE_LIMIT_PER_MINUTE, ventana_segundos: float = RATE_LIMIT_WINDOW_SECONDS):
        self._limite = limite_por_minuto
        self._ventana = ventana_segundos
        self._lock = threading.Lock()
        self._golpes: dict[str, list[float]] = {}

    def permitir(self, token: str, *, ahora: float | None = None) -> bool:
        ahora = time.monotonic() if ahora is None else ahora
        with self._lock:
            golpes = [t for t in self._golpes.get(token, []) if ahora - t < self._ventana]
            if len(golpes) >= self._limite:
                self._golpes[token] = golpes
                return False
            golpes.append(ahora)
            self._golpes[token] = golpes
            return True

    def reiniciar(self):
        with self._lock:
            self._golpes.clear()


limitador_de_tasa = LimitadorDeTasa()


def _token_esperado() -> str:
    token = os.environ.get("BUSINESS_CONTEXT_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="BUSINESS_CONTEXT_TOKEN no está configurado")
    return token


def requerir_token_negocio(authorization: str | None = Header(default=None)) -> str:
    """Autentica con BUSINESS_CONTEXT_TOKEN -- un token de servicio NUEVO
    y dedicado, comparado con hmac.compare_digest, NUNCA la sesión de
    admin humano (api/admin.py::requerir_admin es un mecanismo distinto,
    para un actor distinto -- nunca se reutiliza acá ni se sirve como
    alternativa)."""
    esperado = _token_esperado()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token de servicio requerido")
    suministrado = authorization[len("Bearer "):]
    if not hmac.compare_digest(suministrado, esperado):
        raise HTTPException(status_code=401, detail="Token de servicio inválido")
    if not limitador_de_tasa.permitir(suministrado):
        raise HTTPException(status_code=429, detail="Límite de tasa excedido (2 solicitudes/minuto)")
    return suministrado


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fecha_sqlite_a_iso(valor: str | None) -> str | None:
    """`fecha_creacion`/`fecha_actualizacion` se guardan como
    "%Y-%m-%d %H:%M:%S" en hora local del proceso -- se reexpresan en un
    formato ISO-8601 explícito (sin inventar una zona horaria distinta a
    la que ya tenían)."""
    if not valor:
        return None
    return valor.replace(" ", "T") + "Z"


def _data_as_of_eventos(dias):
    """MAX(fecha_creacion) sobre `eventos`, en el mismo filtro de fecha
    que la query de negocio ya aplicó -- None + razón explícita si no hay
    filas (M9 Corrección 2/10: nunca inventado, nunca conflacionado con
    "cuándo se llamó")."""
    conexion = conectar()
    filtro, params = eventos._filtro_fecha(dias)
    fila = conexion.execute(f"SELECT MAX(fecha_creacion) AS maximo FROM eventos WHERE 1=1 {filtro}", params).fetchone()
    conexion.close()
    maximo = fila["maximo"] if fila else None
    if maximo is None:
        return None, "sin eventos registrados en el rango consultado"
    return _fecha_sqlite_a_iso(maximo), None


@router.get("/matching-failure-summary")
def matching_failure_summary(dias: int | None = None, _token: str = Depends(requerir_token_negocio)):
    """Materiales/categorías con mayor tasa de rechazo del motor de
    selección automática -- SIEMPRE agrupado por `categoria`, nunca por
    `texto_material` crudo (M9 Corrección 1 de V2). Reutiliza únicamente
    la lógica de ranking de `eventos._desempeno_agrupado`."""
    crudo = eventos._desempeno_agrupado("categoria", MAX_TOP_N + 1, dias, minimo_sugerencias=3)
    truncated = len(crudo) > MAX_TOP_N
    categorias = crudo[:MAX_TOP_N]
    for fila in categorias:
        fila["categoria"] = truncar_texto(fila["categoria"])

    # Materiales que el motor de matching nunca reconoció (sin categoria/
    # SKU normalizado) -- expuestos SOLO como un conteo genérico, nunca
    # con el texto original del plano (M9 Corrección 1).
    conexion = conectar()
    filtro, params = eventos._filtro_fecha(dias)
    fila_no_catalogado = conexion.execute(
        f"""
        SELECT COUNT(*) AS sugeridas
        FROM eventos
        WHERE categoria IS NULL AND {eventos._FILTRO_SUGERENCIA_AUTOMATICA}
          AND tipo = ? {filtro}
        """,
        (eventos.TIPO_ITEM_AGREGADO, *params),
    ).fetchone()
    conexion.close()
    material_no_catalogado = {
        "etiqueta": MATERIAL_NO_CATALOGADO_LABEL,
        "sugeridas": fila_no_catalogado["sugeridas"] if fila_no_catalogado else 0,
    }

    data_as_of, data_as_of_reason = _data_as_of_eventos(dias)
    evidence_status = EVIDENCE_SUFICIENTE if (categorias or material_no_catalogado["sugeridas"]) else EVIDENCE_INSUFICIENTE

    return {
        "categorias": categorias,
        "material_no_catalogado": material_no_catalogado,
        "truncated": truncated,
        "data_as_of": data_as_of,
        "data_as_of_reason": data_as_of_reason,
        "computed_at": _ahora_iso(),
        "evidence_status": evidence_status,
    }


@router.get("/selection-accuracy")
def selection_accuracy(dias: int | None = None, _token: str = Depends(requerir_token_negocio)):
    """Precisión/tasa de aceptación del motor de selección automática --
    envoltorio de solo lectura sobre `eventos.resumen_seleccion_automatica`,
    que ya agrega por `confianza_match`, nunca por texto libre."""
    crudo = eventos.resumen_seleccion_automatica(dias=dias)
    data_as_of, data_as_of_reason = _data_as_of_eventos(dias)
    evidence_status = EVIDENCE_SUFICIENTE if crudo["total"]["sugeridas"] else EVIDENCE_INSUFICIENTE
    return {
        "por_confianza": crudo["por_confianza"],
        "total": crudo["total"],
        "data_as_of": data_as_of,
        "data_as_of_reason": data_as_of_reason,
        "computed_at": _ahora_iso(),
        "evidence_status": evidence_status,
    }


@router.get("/catalog-coverage-by-supplier")
def catalog_coverage_by_supplier(_token: str = Depends(requerir_token_negocio)):
    """Conteo de productos en catálogo por proveedor -- agregado directo
    sobre `productos`, nunca sobre `usuarios`/`proyectos`."""
    conexion = conectar()
    filas = conexion.execute(
        """
        SELECT proveedor, COUNT(*) AS cantidad
        FROM productos
        WHERE proveedor IS NOT NULL
        GROUP BY proveedor
        ORDER BY cantidad DESC
        LIMIT ?
        """,
        (MAX_TOP_N + 1,),
    ).fetchall()
    fila_fecha = conexion.execute("SELECT MAX(fecha_actualizacion) AS maximo FROM productos").fetchone()
    conexion.close()

    truncated = len(filas) > MAX_TOP_N
    proveedores = [
        {"proveedor": truncar_texto(fila["proveedor"]), "cantidad": fila["cantidad"]}
        for fila in filas[:MAX_TOP_N]
    ]
    maximo = fila_fecha["maximo"] if fila_fecha else None
    data_as_of = _fecha_sqlite_a_iso(maximo)
    data_as_of_reason = None if data_as_of else "ningún producto tiene fecha_actualizacion registrada"
    evidence_status = EVIDENCE_SUFICIENTE if proveedores else EVIDENCE_INSUFICIENTE

    return {
        "proveedores": proveedores,
        "truncated": truncated,
        "data_as_of": data_as_of,
        "data_as_of_reason": data_as_of_reason,
        "computed_at": _ahora_iso(),
        "evidence_status": evidence_status,
    }
