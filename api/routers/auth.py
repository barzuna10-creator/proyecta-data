"""Endpoints de autenticación (ver api/auth.py y BETA_1.0_CHECKLIST.md,
hallazgo 4.1). Router nuevo, aislado -- no toca proyectos.py ni
sistemas_constructivos.py."""

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from api.auth import ErrorAutenticacion, cerrar_sesion, iniciar_sesion, obtener_usuario, registrar_usuario
from api.identidad import obtener_propietario_id

router = APIRouter(prefix="/auth", tags=["auth"])


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


@router.post("/login")
def login(body: LoginRequest):
    try:
        return iniciar_sesion(body.email, body.password)
    except ErrorAutenticacion as error:
        raise HTTPException(status_code=401, detail=str(error))


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
