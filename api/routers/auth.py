"""Endpoints de autenticación (ver api/auth.py y BETA_1.0_CHECKLIST.md,
hallazgo 4.1). Router nuevo, aislado -- no toca proyectos.py ni
sistemas_constructivos.py."""

import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from api.auth import ErrorAutenticacion, cerrar_sesion, iniciar_sesion, obtener_usuario, registrar_usuario
from api.identidad import obtener_propietario_id
from api.observabilidad import logger
from database.migraciones import migraciones_completadas

router = APIRouter(prefix="/auth", tags=["auth"])


def _manejar_si_falta_tabla(error: sqlite3.OperationalError, endpoint: str):
    """Causa raíz real de producción (ver database/migraciones.py): un
    500 genérico acá no dice si el problema es del cliente o de que el
    runner de migraciones todavía no terminó (o falló) en este proceso.
    Si es específicamente "no such table", se vuelve un 503 diagnosticable
    -- con qué tabla falta y cuáles de las migraciones registradas sí
    llegaron a aplicarse -- y NO se oculta el error original (se relanza
    después de loguear)."""
    mensaje = str(error)
    if "no such table" not in mensaje:
        raise error

    tabla_faltante = mensaje.split("no such table:")[-1].strip()
    completadas = migraciones_completadas()
    logger.error(
        f"ESQUEMA_NO_LISTO endpoint={endpoint} tabla_faltante={tabla_faltante} "
        f"migraciones_completadas={completadas} -- el esquema todavía no está listo en este "
        "proceso (el runner de database/migraciones.py no terminó o falló), no es un error del cliente"
    )
    raise HTTPException(
        status_code=503,
        detail="El servidor está terminando de preparar la base de datos. Probá de nuevo en unos segundos.",
    ) from error


class RegistroRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)
    nombre: str | None = None


class LoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


@router.post("/registro")
def registro(body: RegistroRequest):
    try:
        return registrar_usuario(body.email, body.password, body.nombre)
    except ErrorAutenticacion as error:
        raise HTTPException(status_code=422, detail=str(error))
    except sqlite3.OperationalError as error:
        _manejar_si_falta_tabla(error, "/auth/registro")


@router.post("/login")
def login(body: LoginRequest):
    try:
        return iniciar_sesion(body.email, body.password)
    except ErrorAutenticacion as error:
        raise HTTPException(status_code=401, detail=str(error))
    except sqlite3.OperationalError as error:
        _manejar_si_falta_tabla(error, "/auth/login")


@router.post("/logout", status_code=204)
def logout(authorization: str = Header(default="")):
    # No usa Depends(obtener_propietario_id) a propósito: cerrar sesión
    # con un token ya inválido/expirado también debe responder 204, no
    # 401 -- el resultado que le importa al usuario ("ya no hay sesión")
    # es el mismo en ambos casos.
    if authorization.startswith("Bearer "):
        cerrar_sesion(authorization.removeprefix("Bearer ").strip())


@router.get("/yo")
def yo(propietario_id: str = Depends(obtener_propietario_id)):
    """"Quién soy" -- el frontend la usa para confirmar, al cargar la
    app, que el token guardado todavía sirve, sin tener que esperar a
    que falle la primera petición real."""
    usuario = obtener_usuario(propietario_id)
    if not usuario:
        raise HTTPException(status_code=401, detail="Sesión inválida.")
    return usuario
