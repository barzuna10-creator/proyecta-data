"""Migración aditiva para autenticación real (ver BETA_1.0_CHECKLIST.md,
hallazgo 4.1: "no existe autenticación real -- la identidad de cada
usuario es un X-Propietario-Id que el propio navegador genera, sin
ninguna verificación de servidor").

Tres tablas nuevas, ninguna columna existente se toca:

- `usuarios`: cuentas reales, con contraseña con hash (PBKDF2-HMAC-SHA256,
  200,000 iteraciones, sal por usuario -- sin dependencias nuevas, todo
  con hashlib de la librería estándar). `usuarios.id` reemplaza, para
  cuentas nuevas, al UUID que antes generaba el navegador -- mismo tipo
  de valor (string opaco), así que `proyectos.propietario_id` y todo lo
  que ya filtra por ese campo sigue funcionando sin ningún cambio de
  esquema ahí.
- `sesiones`: igual patrón que `proyectos.token_compartido` (token
  aleatorio opaco, `secrets.token_urlsafe`), pero server-side y con
  expiración -- es lo que permite verificar identidad de verdad en vez
  de confiar en lo que el cliente diga.
- `feedback`: canal de feedback dentro del producto (BETA_1.0_CHECKLIST.md,
  hallazgo 7.1). `usuario_id` es NULLABLE a propósito: si el feedback
  llega justo cuando la sesión ya expiró, igual se guarda (ON DELETE
  SET NULL), nunca se descarta un reporte real por un problema de sesión.

No se toca ninguna columna existente. Reejecutar este script no debe
fallar ni duplicar nada.
"""

import sqlite3

from db import BASE_DATOS


def main():
    conexion = sqlite3.connect(BASE_DATOS)
    conexion.execute("PRAGMA foreign_keys = ON")
    cursor = conexion.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            nombre TEXT,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            fecha_creacion TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sesiones (
            token TEXT PRIMARY KEY,
            usuario_id TEXT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            fecha_creacion TEXT NOT NULL,
            fecha_expiracion TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sesiones_usuario
        ON sesiones (usuario_id)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id TEXT REFERENCES usuarios(id) ON DELETE SET NULL,
            mensaje TEXT NOT NULL,
            pagina TEXT,
            fecha_creacion TEXT NOT NULL
        )
    """)

    conexion.commit()
    conexion.close()

    print("✅ Tablas de autenticación y feedback creadas correctamente.")


if __name__ == "__main__":
    main()
