import os
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from api.identidad import obtener_propietario_id
from api import repositorio_proyectos as repo

router = APIRouter(prefix="/proyectos", tags=["proyectos"])

TAMANO_MAXIMO_PLANO_BYTES = 300 * 1024 * 1024  # generoso: planos reales de referencia pesan 48-110 MB


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
    # Los techos son deliberadamente generosos -- no son una regla de
    # negocio ("ningún margen supera X"), son una red contra un error de
    # dedo (un cero de más) que de otra forma llega sin aviso a un total
    # que se le cotiza a un cliente real (ver PRODUCTION_READINESS_REVIEW.md,
    # hallazgo E7).
    area_m2: float | None = Field(default=None, gt=0, le=100_000)
    indirectos_porcentaje: float | None = Field(default=None, ge=0, le=1000)
    imprevistos_porcentaje: float | None = Field(default=None, ge=0, le=1000)
    margen_porcentaje: float | None = Field(default=None, ge=0, le=1000)

    _validar_nombre = field_validator("nombre")(_no_vacio)


class AgregarItemRequest(BaseModel):
    proveedor: str
    id_proveedor: str
    # Techo generoso, no una regla de negocio -- ver el comentario en
    # ActualizarProyectoRequest más arriba.
    cantidad: float = Field(default=1, gt=0, le=1_000_000)
    # Trazabilidad (ver AUDITORIA_INTEGRAL_PRODUCTO.md, hallazgo §1): todos
    # opcionales porque no todo caller los conoce (un ítem agregado a mano
    # desde el comparador no tiene página ni lámina fuente), pero cuando
    # vienen se guardan tal cual y nunca se pierden ni se fusionan.
    origen: str | None = None
    pagina_fuente: int | None = None
    lamina_fuente: str | None = None
    texto_original: str | None = None
    confianza: str | None = None
    regla_generadora: str | None = None
    # PRODUCTION_READINESS_REVIEW.md, hallazgo E1: la columna existía desde
    # antes pero nunca se escribía -- sin ella, "4.4" en la lista del
    # proyecto no dice si son 4.4 m², 4.4 sacos o 4.4 unidades.
    unidad_medida: str | None = None


class ActualizarItemRequest(BaseModel):
    cantidad: float | None = Field(default=None, gt=0, le=1_000_000)
    estado: str | None = None
    prioridad: str | None = None
    comentario: str | None = None
    partida: str | None = None


CAMPOS_INTERNOS_PROYECTO = {"propietario_id", "indirectos_porcentaje", "imprevistos_porcentaje", "margen_porcentaje"}
CAMPOS_INTERNOS_ITEM = {
    "comentario", "origen", "pagina_fuente", "lamina_fuente",
    "texto_original", "confianza", "regla_generadora",
}


@router.get("/compartido/{token}")
def obtener_proyecto_compartido(token: str):
    proyecto = repo.obtener_proyecto(token=token)

    if not proyecto:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    # PRODUCTION_READINESS_REVIEW.md, hallazgo F2: este endpoint es público
    # y sin autenticación (cualquiera con el link lo puede ver) -- antes
    # solo se quitaba propietario_id (la credencial que autoriza todas las
    # demás operaciones, ver api/identidad.py), pero se dejaba pasar el
    # margen/indirectos/imprevistos internos del ingeniero y, por ítem, el
    # comentario interno y la metadata de trazabilidad (de dónde salió
    # cada ítem, con qué confianza) -- nada de eso es información para un
    # cliente que solo debería ver la cotización final.
    for campo in CAMPOS_INTERNOS_PROYECTO:
        proyecto.pop(campo, None)
    # cotizacion.partidas[].items reutiliza los MISMOS dicts de items (ver
    # _agrupar_por_partida en repositorio_proyectos.py) -- filtrar acá
    # también los filtra ahí, no hace falta recorrer ambos.
    for item in proyecto.get("items", []):
        for campo in CAMPOS_INTERNOS_ITEM:
            item.pop(campo, None)

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
            origen=body.origen,
            pagina_fuente=body.pagina_fuente,
            lamina_fuente=body.lamina_fuente,
            texto_original=body.texto_original,
            confianza=body.confianza,
            regla_generadora=body.regla_generadora,
            unidad_medida=body.unidad_medida,
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


@router.post("/{proyecto_id}/plano")
def subir_plano(
    proyecto_id: int,
    archivo: UploadFile = File(...),
    propietario_id: str = Depends(obtener_propietario_id),
):
    if archivo.content_type != "application/pdf":
        raise HTTPException(status_code=422, detail="El archivo debe ser un PDF.")

    # Sin streaming a propósito: lectura_planos necesita una ruta en disco
    # (fitz.open(ruta)), y los dos planos de referencia usados para
    # calibrar todo lectura_planos pesan 48-110 MB -- muy por debajo de
    # cualquier límite razonable de memoria para un archivo temporal.
    archivo_temporal = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        try:
            tamano = 0
            while fragmento := archivo.file.read(1024 * 1024):
                tamano += len(fragmento)
                if tamano > TAMANO_MAXIMO_PLANO_BYTES:
                    raise HTTPException(status_code=413, detail="El PDF es demasiado grande.")
                archivo_temporal.write(fragmento)
        finally:
            archivo_temporal.close()

        try:
            proyecto = repo.analizar_plano(
                proyecto_id, propietario_id, archivo_temporal.name, archivo.filename
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=422,
                detail="No se pudo leer el PDF. Verificá que no esté dañado o protegido.",
            )
    finally:
        os.unlink(archivo_temporal.name)

    if not proyecto:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    return proyecto


@router.delete("/{proyecto_id}/plano")
def eliminar_plano(
    proyecto_id: int,
    propietario_id: str = Depends(obtener_propietario_id),
):
    proyecto = repo.eliminar_plano(proyecto_id, propietario_id)

    if not proyecto:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    return proyecto
