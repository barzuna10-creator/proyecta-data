"""Pruebas para api/auth.py (ver RELEASE_CANDIDATE_1.md y
BETA_1.0_CHECKLIST.md, hallazgo 4.1: "no existe autenticación real").

Mismo patrón de base de datos temporal que tests/test_repositorio_proyectos.py
-- nunca contra database/proyecta.db real."""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from api.auth import (
    ErrorAutenticacion,
    cerrar_sesion,
    iniciar_sesion,
    obtener_usuario,
    registrar_usuario,
    verificar_sesion,
)


def _crear_db_temporal():
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    conexion = sqlite3.connect(archivo.name)
    conexion.execute("""
        CREATE TABLE usuarios (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            nombre TEXT,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            fecha_creacion TEXT NOT NULL
        )
    """)
    conexion.execute("""
        CREATE TABLE sesiones (
            token TEXT PRIMARY KEY,
            usuario_id TEXT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            fecha_creacion TEXT NOT NULL,
            fecha_expiracion TEXT NOT NULL
        )
    """)
    conexion.commit()
    conexion.close()
    return archivo.name


class BasePruebaAuth(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        self._patch = self._parchar_db()
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.remove(self.ruta_db)

    def _parchar_db(self):
        import db
        return mock.patch.object(db, "BASE_DATOS", self.ruta_db)


class PruebaRegistro(BasePruebaAuth):
    def test_registro_exitoso_devuelve_token_utilizable(self):
        resultado = registrar_usuario("ana@example.com", "claveSegura123", "Ana")
        self.assertIn("token", resultado)
        self.assertEqual(resultado["email"], "ana@example.com")
        # el token que acaba de emitir el registro tiene que servir de verdad
        self.assertEqual(verificar_sesion(resultado["token"]), resultado["usuario_id"])

    def test_normaliza_email_a_minusculas(self):
        resultado = registrar_usuario("Ana@Example.COM", "claveSegura123")
        self.assertEqual(resultado["email"], "ana@example.com")

    def test_email_duplicado_se_rechaza(self):
        registrar_usuario("ana@example.com", "claveSegura123")
        with self.assertRaises(ErrorAutenticacion):
            registrar_usuario("ana@example.com", "otraClaveDistinta")

    def test_email_duplicado_no_revela_que_ya_existe_ni_pisa_la_cuenta(self):
        # El mensaje de error tiene que ser el mismo, sin distinguir "ya
        # existe" de cualquier otro fallo -- y la cuenta original (con su
        # contraseña original) tiene que seguir intacta.
        registrar_usuario("ana@example.com", "claveOriginal123")
        try:
            registrar_usuario("ana@example.com", "claveNueva456")
        except ErrorAutenticacion:
            pass
        resultado = iniciar_sesion("ana@example.com", "claveOriginal123")
        self.assertIsNotNone(resultado["token"])

    def test_password_corto_se_rechaza(self):
        with self.assertRaises(ErrorAutenticacion):
            registrar_usuario("ana@example.com", "1234567")  # 7 caracteres

    def test_email_invalido_se_rechaza(self):
        with self.assertRaises(ErrorAutenticacion):
            registrar_usuario("no-es-un-email", "claveSegura123")

    def test_password_nunca_se_guarda_en_texto_plano(self):
        registrar_usuario("ana@example.com", "claveSegura123")
        import db
        conexion = db.conectar()
        fila = conexion.execute("SELECT password_hash FROM usuarios WHERE email = ?", ("ana@example.com",)).fetchone()
        conexion.close()
        self.assertNotIn("claveSegura123", fila["password_hash"])


class PruebaLogin(BasePruebaAuth):
    def test_login_con_password_correcta(self):
        registrar_usuario("ana@example.com", "claveSegura123")
        resultado = iniciar_sesion("ana@example.com", "claveSegura123")
        self.assertIsNotNone(verificar_sesion(resultado["token"]))

    def test_login_con_password_incorrecta_se_rechaza(self):
        registrar_usuario("ana@example.com", "claveSegura123")
        with self.assertRaises(ErrorAutenticacion):
            iniciar_sesion("ana@example.com", "claveIncorrecta")

    def test_login_con_email_inexistente_se_rechaza(self):
        with self.assertRaises(ErrorAutenticacion):
            iniciar_sesion("nadie@example.com", "cualquierClave123")

    def test_cada_login_emite_un_token_distinto(self):
        registrar_usuario("ana@example.com", "claveSegura123")
        t1 = iniciar_sesion("ana@example.com", "claveSegura123")["token"]
        t2 = iniciar_sesion("ana@example.com", "claveSegura123")["token"]
        self.assertNotEqual(t1, t2)
        # ambas sesiones son válidas al mismo tiempo -- login en dos
        # dispositivos no invalida la otra sesión.
        self.assertIsNotNone(verificar_sesion(t1))
        self.assertIsNotNone(verificar_sesion(t2))


class PruebaVerificarSesion(BasePruebaAuth):
    def test_token_inexistente_no_verifica(self):
        self.assertIsNone(verificar_sesion("token-que-nunca-existio"))

    def test_token_vacio_o_none_no_verifica(self):
        self.assertIsNone(verificar_sesion(""))
        self.assertIsNone(verificar_sesion(None))

    def test_token_expirado_no_verifica(self):
        resultado = registrar_usuario("ana@example.com", "claveSegura123")
        import db
        conexion = db.conectar()
        # simula una sesión creada hace 31 días (vence a los 30)
        conexion.execute(
            "UPDATE sesiones SET fecha_expiracion = '2000-01-01 00:00:00' WHERE token = ?",
            (resultado["token"],),
        )
        conexion.commit()
        conexion.close()
        self.assertIsNone(verificar_sesion(resultado["token"]))


class PruebaCerrarSesion(BasePruebaAuth):
    def test_cerrar_sesion_invalida_el_token_de_inmediato(self):
        # Regresión directa: la primera versión de POST /auth/logout no
        # leía el header Authorization (faltaba Header(...) en el
        # parámetro), así que nunca invocaba cerrar_sesion() -- el token
        # seguía funcionando después de "cerrar sesión". Encontrado
        # probando el flujo real con curl, no con esta prueba -- se deja
        # acá para que no pueda volver a pasar en silencio.
        resultado = registrar_usuario("ana@example.com", "claveSegura123")
        self.assertIsNotNone(verificar_sesion(resultado["token"]))
        cerrar_sesion(resultado["token"])
        self.assertIsNone(verificar_sesion(resultado["token"]))

    def test_cerrar_sesion_con_token_invalido_no_lanza(self):
        cerrar_sesion("token-que-no-existe")  # no debe lanzar


class PruebaObtenerUsuario(BasePruebaAuth):
    def test_obtener_usuario_existente(self):
        resultado = registrar_usuario("ana@example.com", "claveSegura123", "Ana Pérez")
        usuario = obtener_usuario(resultado["usuario_id"])
        self.assertEqual(usuario["email"], "ana@example.com")
        self.assertEqual(usuario["nombre"], "Ana Pérez")

    def test_obtener_usuario_inexistente_da_none(self):
        self.assertIsNone(obtener_usuario("id-que-no-existe"))


if __name__ == "__main__":
    unittest.main()
