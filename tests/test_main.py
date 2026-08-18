"""Pruebas para GET /health y GET /version (api/main.py), ver
MASTER_ROADMAP.md, NOW #1: "no existe ningún mecanismo" para detectar un
backend roto o un catálogo vacío -- antes de esto, "/" respondía 200 sin
tocar la base de datos.

No hay TestClient de FastAPI a propósito (requeriría httpx, no instalado)
-- salud() y version() son funciones Python normales sin `async def` ni
Depends() de request real, así que se llaman directo, mismo patrón que
tests/test_feedback.py y tests/test_routers_proyectos.py."""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import db
from api.main import salud, version


def _crear_db_temporal(con_productos=True, cantidad_productos=0):
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    if con_productos:
        conexion = sqlite3.connect(archivo.name)
        conexion.execute("""
            CREATE TABLE productos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                precio REAL
            )
        """)
        for i in range(cantidad_productos):
            conexion.execute(
                "INSERT INTO productos (nombre, precio) VALUES (?, ?)",
                (f"Producto {i}", 1000),
            )
        conexion.commit()
        conexion.close()

    return archivo.name


def _cuerpo(respuesta):
    return json.loads(respuesta.body)


class PruebaSaludBaseYCatalogoOk(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal(con_productos=True, cantidad_productos=3)
        self._patch = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.remove(self.ruta_db)

    def test_devuelve_200_con_base_conectada_y_catalogo_no_vacio(self):
        respuesta = salud()

        self.assertEqual(respuesta.status_code, 200)
        cuerpo = _cuerpo(respuesta)
        self.assertEqual(cuerpo["status"], "ok")
        self.assertEqual(cuerpo["checks"]["database"], "ok")
        self.assertEqual(cuerpo["checks"]["catalog"], "ok")


class PruebaSaludCatalogoVacio(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal(con_productos=True, cantidad_productos=0)
        self._patch = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.remove(self.ruta_db)

    def test_devuelve_503_cuando_productos_tiene_cero_filas(self):
        respuesta = salud()

        self.assertEqual(respuesta.status_code, 503)
        cuerpo = _cuerpo(respuesta)
        self.assertEqual(cuerpo["status"], "degraded")
        self.assertEqual(cuerpo["checks"]["database"], "ok")
        self.assertEqual(cuerpo["checks"]["catalog"], "empty")


class PruebaSaludBaseInalcanzable(unittest.TestCase):
    def setUp(self):
        # Un directorio no es un archivo SQLite válido -- conectar() (o el
        # primer execute()) falla de forma confiable sin necesidad de
        # simular una desconexión de red real.
        self.directorio_temporal = tempfile.mkdtemp()
        self._patch = mock.patch.object(db, "BASE_DATOS", self.directorio_temporal)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.rmdir(self.directorio_temporal)

    def test_devuelve_503_cuando_la_base_no_se_puede_abrir(self):
        respuesta = salud()

        self.assertEqual(respuesta.status_code, 503)
        cuerpo = _cuerpo(respuesta)
        self.assertEqual(cuerpo["status"], "degraded")
        self.assertEqual(cuerpo["checks"]["database"], "error")

    def test_la_respuesta_no_filtra_detalles_internos(self):
        respuesta = salud()
        cuerpo = _cuerpo(respuesta)
        texto_crudo = respuesta.body.decode("utf-8")

        # Ni la ruta real de la base de datos (el directorio temporal usado
        # en esta prueba), ni palabras propias de un traceback/excepción de
        # sqlite3, deben aparecer en el cuerpo de la respuesta pública.
        self.assertNotIn(self.directorio_temporal, texto_crudo)
        for fragmento_prohibido in ("Traceback", "sqlite3", "Error:", "Exception"):
            self.assertNotIn(fragmento_prohibido, texto_crudo)

        # Solo las dos claves de chequeo esperadas, con valores de un
        # conjunto fijo -- nada de mensajes libres.
        self.assertEqual(set(cuerpo["checks"].keys()), {"database", "catalog"})
        self.assertIn(cuerpo["checks"]["database"], {"ok", "error"})
        self.assertIn(cuerpo["checks"]["catalog"], {"ok", "empty", "unknown"})


class PruebaVersion(unittest.TestCase):
    def test_no_toca_la_base_de_datos(self):
        # BASE_DATOS apunta a una ruta que no existe y no se crea -- si
        # version() tocara la base de alguna forma, esto lo haría fallar.
        ruta_inexistente = os.path.join(tempfile.gettempdir(), "no-existe-zentra-version-test.db")
        with mock.patch.object(db, "BASE_DATOS", ruta_inexistente):
            respuesta = version()

        self.assertFalse(os.path.exists(ruta_inexistente))
        self.assertIn("version", respuesta)
        self.assertIn("commit", respuesta)

    def test_usa_unknown_cuando_render_git_commit_no_esta_seteada(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RENDER_GIT_COMMIT", None)
            respuesta = version()

        self.assertEqual(respuesta["commit"], "unknown")

    def test_usa_render_git_commit_cuando_esta_seteada(self):
        with mock.patch.dict(os.environ, {"RENDER_GIT_COMMIT": "abc1234"}):
            respuesta = version()

        self.assertEqual(respuesta["commit"], "abc1234")

    def test_funciona_incluso_si_health_reportaria_degradado(self):
        # /version debe responder aunque la base esté inalcanzable --
        # identidad de despliegue es una preocupación distinta de salud.
        directorio_temporal = tempfile.mkdtemp()
        try:
            with mock.patch.object(db, "BASE_DATOS", directorio_temporal):
                respuesta_salud = salud()
                respuesta_version = version()

            self.assertEqual(respuesta_salud.status_code, 503)
            self.assertIn("version", respuesta_version)
        finally:
            os.rmdir(directorio_temporal)


if __name__ == "__main__":
    unittest.main()
