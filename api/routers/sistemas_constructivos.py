"""Expone sistemas_constructivos.py (ver SISTEMAS_CONSTRUCTIVOS_V1.md) vía
HTTP -- el único hueco real que tenía la biblioteca para poder usarse desde
un flujo de usuario: no tocaba ningún endpoint ni ninguna tabla, era una
librería Python pura sin forma de llegar al frontend.

Deliberadamente sin tabla ni persistencia propia: la biblioteca es
conocimiento de referencia fijo (no cambia por usuario ni por proyecto), y
el resultado de calcular_materiales() nunca se guarda como tal -- se
convierte en un ItemProyecto real recién cuando el usuario busca un
producto puntual y lo confirma, exactamente el mismo patrón que ya usan
las plantillas de app/lib/plantillasProyecto.ts vía
POST /proyectos/{id}/items (sin cambios, se reutiliza tal cual)."""

from fastapi import APIRouter, HTTPException, Query

import sistemas_constructivos as sc

router = APIRouter(prefix="/sistemas-constructivos", tags=["sistemas-constructivos"])


def _serializar_sistema(sistema):
    return {
        "id": sistema.id,
        "nombre": sistema.nombre,
        "descripcion": sistema.descripcion,
        "dimension": sistema.dimension.value,
    }


def _serializar_linea(linea):
    return {
        "sistema_origen": linea.sistema_origen,
        "material_id": linea.material_id,
        "nombre": linea.nombre,
        "termino_busqueda": linea.termino_busqueda,
        "unidad_compra": linea.unidad_compra.value,
        "cantidad": linea.cantidad,
        "opcional": linea.opcional,
        "justificacion": linea.justificacion,
        "contexto": linea.contexto,
    }


@router.get("")
def listar_sistemas():
    return [_serializar_sistema(sistema) for sistema in sc.REGISTRO.values()]


@router.get("/{sistema_id}/calcular")
def calcular_materiales(sistema_id: str, cantidad: float = Query(..., gt=0)):
    if sistema_id not in sc.REGISTRO:
        raise HTTPException(status_code=404, detail="Sistema constructivo no encontrado")

    sistema = sc.REGISTRO[sistema_id]
    lineas = sc.calcular_materiales(sistema_id, cantidad)

    return {
        "sistema_id": sistema_id,
        "dimension": sistema.dimension.value,
        "cantidad": cantidad,
        "materiales": [_serializar_linea(linea) for linea in lineas],
    }
