"""Dashboards internos sobre la tabla `eventos` (ver eventos.py,
ARQUITECTURA_RECOMENDACION_V2.md Fase 0) -- precisión y aceptación del
motor de selección automática, materiales más difíciles, categorías con
peor desempeño. Solo lectura, ninguna IA/ML acá, exclusivamente
agregación de lo que ya se registró.

`Depends(obtener_propietario_id)` acá NO filtra nada por ese usuario --
se usa únicamente como puerta de autenticación (cualquier cuenta con
sesión válida puede ver estas métricas agregadas de TODOS los usuarios).
No existe hoy un sistema de roles/admin en Proyecta (ver
ARQUITECTURA_RECOMENDACION_V2.md) -- restringir esto a un rol real es una
mejora aparte, deliberadamente fuera de este cambio. Por eso el prefijo
/admin y por eso este router no se referencia desde ningún link de
navegación visible para un usuario normal (ver app/admin/metricas en el
frontend)."""

from fastapi import APIRouter, Depends, Query

from api.identidad import obtener_propietario_id
import eventos

router = APIRouter(prefix="/admin/metricas", tags=["metricas"])


@router.get("/seleccion-automatica")
def resumen_seleccion_automatica(
    dias: int | None = Query(default=None, gt=0),
    _usuario_autenticado: str = Depends(obtener_propietario_id),
):
    return eventos.resumen_seleccion_automatica(dias=dias)


@router.get("/materiales-dificiles")
def materiales_mas_dificiles(
    limite: int = Query(default=20, gt=0, le=200),
    dias: int | None = Query(default=None, gt=0),
    _usuario_autenticado: str = Depends(obtener_propietario_id),
):
    return {"materiales": eventos.materiales_mas_dificiles(limite=limite, dias=dias)}


@router.get("/categorias-peor-desempeno")
def categorias_peor_desempeno(
    limite: int = Query(default=20, gt=0, le=200),
    dias: int | None = Query(default=None, gt=0),
    _usuario_autenticado: str = Depends(obtener_propietario_id),
):
    return {"categorias": eventos.categorias_peor_desempeno(limite=limite, dias=dias)}
