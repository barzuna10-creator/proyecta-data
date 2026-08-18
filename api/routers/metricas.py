"""Dashboards internos sobre la tabla `eventos` (ver eventos.py,
ARQUITECTURA_RECOMENDACION_V2.md Fase 0) -- precisión y aceptación del
motor de selección automática, materiales más difíciles, categorías con
peor desempeño. Solo lectura, ninguna IA/ML acá, exclusivamente
agregación de lo que ya se registró.

`Depends(requerir_admin)` (ver api/admin.py) exige sesión válida Y que
ese usuario_id esté en el allowlist ADMIN_USUARIO_IDS -- antes acá se
usaba `Depends(obtener_propietario_id)` solo, que autenticaba pero no
autorizaba (cualquier cuenta autoregistrada podía ver estas métricas
agregadas de TODOS los usuarios, texto_material crudo de planos ajenos
incluido). Por eso el prefijo /admin y por eso este router no se
referencia desde ningún link de navegación visible para un usuario
normal (ver app/admin/metricas en el frontend) -- pero eso nunca fue,
por sí solo, control de acceso real."""

from fastapi import APIRouter, Depends, Query

from api.admin import requerir_admin
import eventos

router = APIRouter(prefix="/admin/metricas", tags=["metricas"])


@router.get("/seleccion-automatica")
def resumen_seleccion_automatica(
    dias: int | None = Query(default=None, gt=0),
    _usuario_autenticado: str = Depends(requerir_admin),
):
    return eventos.resumen_seleccion_automatica(dias=dias)


@router.get("/materiales-dificiles")
def materiales_mas_dificiles(
    limite: int = Query(default=20, gt=0, le=200),
    dias: int | None = Query(default=None, gt=0),
    _usuario_autenticado: str = Depends(requerir_admin),
):
    return {"materiales": eventos.materiales_mas_dificiles(limite=limite, dias=dias)}


@router.get("/categorias-peor-desempeno")
def categorias_peor_desempeno(
    limite: int = Query(default=20, gt=0, le=200),
    dias: int | None = Query(default=None, gt=0),
    _usuario_autenticado: str = Depends(requerir_admin),
):
    return {"categorias": eventos.categorias_peor_desempeno(limite=limite, dias=dias)}
