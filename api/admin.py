"""Puerta de autorización para endpoints /admin/* (ver
api/routers/metricas.py).

Zentra no tiene hoy un sistema de roles persistente -- `usuarios` (ver
database/agregar_autenticacion.py) no tiene ninguna columna de rol/admin.
Antes, `Depends(obtener_propietario_id)` en /admin/metricas era la única
puerta: autenticaba (sesión válida) pero NUNCA autorizaba, así que
cualquier cuenta autoregistrada podía ver métricas agregadas de TODOS los
usuarios (texto_material crudo de planos ajenos incluido).

Mientras no exista un rol real en la base, la lista de cuentas
autorizadas para /admin/* vive en la variable de entorno
ADMIN_USUARIO_IDS (usuario_id separados por coma -- el mismo valor que
devuelven api/auth.py::registrar_usuario/iniciar_sesion como
"usuario_id"). Sin esa variable configurada, el default es denegar a
TODOS -- nunca queda abierto por accidente si se olvida configurarla."""

import os

from fastapi import Depends, HTTPException

from api.identidad import obtener_propietario_id


def _admin_ids_autorizados():
    crudo = os.environ.get("ADMIN_USUARIO_IDS", "")
    return {valor.strip() for valor in crudo.split(",") if valor.strip()}


def requerir_admin(usuario_id: str = Depends(obtener_propietario_id)):
    """Exige, además de una sesión válida (`obtener_propietario_id`), que
    ese usuario_id esté en el allowlist de administradores. 403 (no 404)
    a propósito -- no hay nada que ocultar sobre si el endpoint existe,
    solo que esta cuenta no tiene acceso."""

    if usuario_id not in _admin_ids_autorizados():
        raise HTTPException(status_code=403, detail="No tenés acceso a esta sección.")

    return usuario_id
