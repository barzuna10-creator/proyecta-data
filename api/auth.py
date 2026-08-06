"""Autenticación real (ver BETA_1.0_CHECKLIST.md, hallazgo 4.1). Reemplaza
el modelo anterior -- un `X-Propietario-Id` que el propio navegador
generaba y enviaba, sin ninguna verificación de servidor -- por cuentas
con contraseña y sesiones verificadas server-side.

Contraseñas: PBKDF2-HMAC-SHA256 con sal por usuario, de la librería
estándar (`hashlib`) -- sin agregar bcrypt/passlib/argon2 como
dependencia nueva. 200,000 iteraciones es el mínimo recomendado por
OWASP (2023) para PBKDF2-SHA256; se puede subir después sin invalidar
contraseñas existentes (el número de iteraciones no se guarda por
usuario en esta versión porque todas las cuentas se crean con el mismo
valor -- si en el futuro se sube, hay que guardar el conteo usado en
cada hash para poder verificar los viejos con su propio valor).

Sesiones: mismo patrón que `proyectos.token_compartido`
(`secrets.token_urlsafe`, ya probado en este código base) -- un token
opaco, aleatorio, que solo existe después de un login exitoso, guardado
en una tabla server-side con expiración. Nunca es el cliente quien
decide su propia identidad.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from db import conectar

ITERACIONES_PBKDF2 = 200_000
DIAS_EXPIRACION_SESION = 30
LONGITUD_MINIMA_PASSWORD = 8


class ErrorAutenticacion(Exception):
    """Cualquier fallo de registro/login con un mensaje seguro para
    mostrarle al usuario -- nunca revela si fue el email o la contraseña
    lo que falló, para no ayudar a enumerar cuentas existentes."""


def _ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _hash_password(password, sal=None):
    if sal is None:
        sal = secrets.token_hex(16)
    derivado = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(sal), ITERACIONES_PBKDF2
    )
    return sal, derivado.hex()


def _verificar_password(password, sal, hash_esperado):
    _, calculado = _hash_password(password, sal)
    return secrets.compare_digest(calculado, hash_esperado)


def _validar_email(email):
    email = (email or "").strip().lower()
    if not email or "@" not in email or " " in email:
        raise ErrorAutenticacion("Ingresá un correo válido.")
    return email


def _validar_password(password):
    if not password or len(password) < LONGITUD_MINIMA_PASSWORD:
        raise ErrorAutenticacion(f"La contraseña debe tener al menos {LONGITUD_MINIMA_PASSWORD} caracteres.")


def _crear_sesion(conexion, usuario_id):
    token = secrets.token_urlsafe(32)
    ahora = datetime.now()
    expiracion = ahora + timedelta(days=DIAS_EXPIRACION_SESION)
    conexion.execute(
        "INSERT INTO sesiones (token, usuario_id, fecha_creacion, fecha_expiracion) VALUES (?, ?, ?, ?)",
        (token, usuario_id, ahora.strftime("%Y-%m-%d %H:%M:%S"), expiracion.strftime("%Y-%m-%d %H:%M:%S")),
    )
    return token


def registrar_usuario(email, password, nombre=None):
    email = _validar_email(email)
    _validar_password(password)

    conexion = conectar()
    existente = conexion.execute("SELECT id FROM usuarios WHERE email = ?", (email,)).fetchone()
    if existente:
        conexion.close()
        # Mismo mensaje genérico que un login fallido -- no confirma ni
        # niega que ese correo específico ya tenga cuenta.
        raise ErrorAutenticacion("No se pudo crear la cuenta. Si ya tenés una, iniciá sesión.")

    usuario_id = secrets.token_urlsafe(16)
    sal, hash_ = _hash_password(password)
    ahora = _ahora()
    conexion.execute(
        "INSERT INTO usuarios (id, email, nombre, password_hash, password_salt, fecha_creacion) VALUES (?, ?, ?, ?, ?, ?)",
        (usuario_id, email, (nombre or "").strip() or None, hash_, sal, ahora),
    )
    token = _crear_sesion(conexion, usuario_id)
    conexion.commit()
    conexion.close()
    return {"usuario_id": usuario_id, "email": email, "nombre": nombre, "token": token}


def iniciar_sesion(email, password):
    email = _validar_email(email)

    conexion = conectar()
    fila = conexion.execute(
        "SELECT id, email, nombre, password_hash, password_salt FROM usuarios WHERE email = ?",
        (email,),
    ).fetchone()

    if not fila or not _verificar_password(password, fila["password_salt"], fila["password_hash"]):
        conexion.close()
        raise ErrorAutenticacion("Correo o contraseña incorrectos.")

    token = _crear_sesion(conexion, fila["id"])
    conexion.commit()
    conexion.close()
    return {"usuario_id": fila["id"], "email": fila["email"], "nombre": fila["nombre"], "token": token}


def cerrar_sesion(token):
    conexion = conectar()
    conexion.execute("DELETE FROM sesiones WHERE token = ?", (token,))
    conexion.commit()
    conexion.close()


def verificar_sesion(token):
    """Devuelve el usuario_id si el token existe y no expiró, None si no.
    Esta es la función que reemplaza, en la práctica, la confianza ciega
    que api/identidad.py le tenía antes al header del cliente."""
    if not token:
        return None

    conexion = conectar()
    fila = conexion.execute(
        "SELECT usuario_id, fecha_expiracion FROM sesiones WHERE token = ?", (token,)
    ).fetchone()
    conexion.close()

    if not fila:
        return None
    if fila["fecha_expiracion"] < _ahora():
        return None
    return fila["usuario_id"]


def obtener_usuario(usuario_id):
    conexion = conectar()
    fila = conexion.execute(
        "SELECT id, email, nombre FROM usuarios WHERE id = ?", (usuario_id,)
    ).fetchone()
    conexion.close()
    return dict(fila) if fila else None
