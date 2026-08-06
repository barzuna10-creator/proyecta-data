"""Canal de feedback dentro del producto (ver BETA_1.0_CHECKLIST.md,
hallazgo 7.1: "ningún canal para que un ingeniero reporte un problema o
dé una opinión dentro del producto"). Un solo endpoint, sin repositorio
aparte -- es un INSERT, no justifica una capa nueva."""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from db import conectar
from api.identidad import obtener_propietario_id

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    mensaje: str = Field(min_length=1, max_length=4000)
    pagina: str | None = None


@router.post("", status_code=204)
def crear_feedback(
    body: FeedbackRequest,
    propietario_id: str = Depends(obtener_propietario_id),
):
    conexion = conectar()
    conexion.execute(
        "INSERT INTO feedback (usuario_id, mensaje, pagina, fecha_creacion) VALUES (?, ?, ?, ?)",
        (propietario_id, body.mensaje.strip(), body.pagina, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conexion.commit()
    conexion.close()
