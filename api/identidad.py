"""Antes (ver BETA_1.0_CHECKLIST.md, hallazgo 4.1): `obtener_propietario_id`
confiaba ciegamente en lo que el cliente mandara en `X-Propietario-Id` --
cualquiera que enviara un UUID ajeno tenía acceso completo a esos
proyectos, porque nada verificaba que quien lo mandaba fuera su dueño
real.

Ahora: el header lleva un token de sesión (`Authorization: Bearer
<token>`), emitido únicamente por api/auth.py después de un login real
con contraseña, y verificado acá contra la tabla `sesiones` en cada
request. Cero cambios en los 11 endpoints que ya usaban
`Depends(obtener_propietario_id)` -- ninguno sabía ni le importaba de
dónde salía el propietario_id, así que quedan protegidos por este único
cambio, sin tocarlos."""

from fastapi import Header, HTTPException

from api.auth import verificar_sesion


def obtener_propietario_id(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Iniciá sesión para continuar.")

    token = authorization.removeprefix("Bearer ").strip()
    usuario_id = verificar_sesion(token)

    if not usuario_id:
        raise HTTPException(status_code=401, detail="Tu sesión expiró o no es válida. Iniciá sesión de nuevo.")

    return usuario_id
