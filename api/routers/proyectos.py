from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from api.identidad import obtener_propietario_id
from api import repositorio_proyectos as repo

router = APIRouter(prefix="/proyectos", tags=["proyectos"])


def _no_vacio(valor):
    if valor is None:
        return valor
    valor = valor.strip()
    if not valor:
        raise ValueError("no puede estar vacío")
    return valor


class CrearProyectoRequest(BaseModel):
    nombre: str = Field(min_length=1)
    comentario: str | None = None
    fecha_objetivo: str | None = None

    _validar_nombre = field_validator("nombre")(_no_vacio)


class ActualizarProyectoRequest(BaseModel):
    nombre: str | None = None
    comentario: str | None = None
    estado: str | None = None
    fecha_objetivo: str | None = None
    cliente: str | None = None
    direccion: str | None = None
    area_m2: float | None = Field(default=None, gt=0)
    indirectos_porcentaje: float | None = Field(default=None, ge=0)
    imprevistos_porcentaje: float | None = Field(default=None, ge=0)
    margen_porcentaje: float | None = Field(default=None, ge=0)

    _validar_nombre = field_validator("nombre")(_no_vacio)


class AgregarItemRequest(BaseModel):
    proveedor: str
    id_proveedor: str
    cantidad: float = Field(default=1, gt=0)


class ActualizarItemRequest(BaseModel):
    cantidad: float | None = Field(default=None, gt=0)
    estado: str | None = None
    prioridad: str | None = None
    comentario: str | None = None
    partida: str | None = None


@router.get("/compartido/{token}")
def obtener_proyecto_compartido(token: str):
    proyecto = repo.obtener_proyecto(token=token)

    if not proyecto:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    # propietario_id es la credencial que autoriza todas las demás operaciones
    # (ver api/identidad.py) -- nunca debe salir por un endpoint público.
    proyecto.pop("propietario_id", None)

    return proyecto


@router.post("")
def crear_proyecto(
    body: CrearProyectoRequest,
    propietario_id: str = Depends(obtener_propietario_id),
):
    return repo.crear_proyecto(
        propietario_id, body.nombre, body.comentario, body.fecha_objetivo
    )


@router.get("")
def listar_proyectos(
    incluir_archivados: bool = False,
    propietario_id: str = Depends(obtener_propietario_id),
):
    return repo.listar_proyectos(propietario_id, incluir_archivados)


@router.get("/{proyecto_id}")
def obtener_proyecto(
    proyecto_id: int,
    propietario_id: str = Depends(obtener_propietario_id),
):
    proyecto = repo.obtener_proyecto(proyecto_id, propietario_id=propietario_id)

    if not proyecto:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    return proyecto


@router.patch("/{proyecto_id}")
def actualizar_proyecto(
    proyecto_id: int,
    body: ActualizarProyectoRequest,
    propietario_id: str = Depends(obtener_propietario_id),
):
    try:
        proyecto = repo.actualizar_proyecto(
            proyecto_id, propietario_id, body.model_dump()
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))

    if not proyecto:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    return proyecto


@router.delete("/{proyecto_id}", status_code=204)
def eliminar_proyecto(
    proyecto_id: int,
    propietario_id: str = Depends(obtener_propietario_id),
):
    eliminado = repo.eliminar_proyecto(proyecto_id, propietario_id)

    if not eliminado:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")


@router.post("/{proyecto_id}/items")
def agregar_item(
    proyecto_id: int,
    body: AgregarItemRequest,
    propietario_id: str = Depends(obtener_propietario_id),
):
    try:
        proyecto = repo.agregar_item(
            proyecto_id,
            propietario_id,
            body.proveedor,
            body.id_proveedor,
            body.cantidad,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))

    if not proyecto:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    return proyecto


@router.patch("/{proyecto_id}/items/{item_id}")
def actualizar_item(
    proyecto_id: int,
    item_id: int,
    body: ActualizarItemRequest,
    propietario_id: str = Depends(obtener_propietario_id),
):
    try:
        proyecto = repo.actualizar_item(
            proyecto_id, propietario_id, item_id, body.model_dump()
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))

    if not proyecto:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    return proyecto


@router.delete("/{proyecto_id}/items/{item_id}")
def eliminar_item(
    proyecto_id: int,
    item_id: int,
    propietario_id: str = Depends(obtener_propietario_id),
):
    proyecto = repo.eliminar_item(proyecto_id, propietario_id, item_id)

    if not proyecto:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    return proyecto
