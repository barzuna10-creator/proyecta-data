"""Pruebas para GET /health y GET /version (api/main.py), ver
MASTER_ROADMAP.md, NOW #1: "no existe ningún mecanismo" para detectar un
backend roto o un catálogo vacío -- antes de esto, "/" respondía 200 sin
tocar la base de datos.

No hay TestClient de FastAPI a propósito (requeriría httpx, no instalado)
-- salud() y version() son funciones Python normales sin `async def` ni
Depends() de request real, así que se llaman directo, mismo patrón que
tests/test_feedback.py y tests/test_routers_proyectos.py.

Reconciliado con las pruebas de GET /productos/{id}
(api/main.py::producto_por_id) -- RELEASE_CANDIDATE.md: reconstruye un
producto desde el backend a partir del id que ya usa /producto/{id} en el
frontend (base64url de url_producto, ver id_producto.py), para que un
link compartido, recargado, o sin sessionStorage siga funcionando. Mismo
patrón: la función de la ruta es una función Python normal, así que se
llama directo -- no hace falta un TestClient ni la dependencia de httpx."""

import base64
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import db
from fastapi import HTTPException

from api.main import producto_por_id, salud, version


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


def _id_de(url):
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def _crear_db_temporal_productos():
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    conexion = sqlite3.connect(archivo.name)
    conexion.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor TEXT, id_proveedor TEXT, sku TEXT, nombre TEXT,
            marca TEXT, categoria TEXT, subcategoria TEXT, precio REAL,
            descripcion TEXT, url_imagen TEXT, url_producto TEXT,
            peso TEXT, imagenes_adicionales TEXT, familia_id INTEGER,
            UNIQUE(proveedor, id_proveedor)
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE familias_producto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor TEXT, categoria TEXT, firma_base TEXT,
            nombre_familia TEXT, fecha_calculo TEXT
        )
        """
    )
    conexion.commit()
    conexion.close()
    return archivo.name


def _insertar_producto(conexion, **campos):
    base = {
        "proveedor": "EPA", "id_proveedor": None, "sku": None, "nombre": None,
        "marca": None, "categoria": None, "subcategoria": None, "precio": 1000,
        "descripcion": None, "url_imagen": None, "url_producto": None,
        "peso": None, "imagenes_adicionales": None, "familia_id": None,
    }
    base.update(campos)
    columnas = ", ".join(base.keys())
    marcadores = ", ".join("?" for _ in base)
    conexion.execute(f"INSERT INTO productos ({columnas}) VALUES ({marcadores})", list(base.values()))


class PruebaProductoPorId(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal_productos()
        self._patch = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.remove(self.ruta_db)

    def _insertar(self, **campos):
        conexion = sqlite3.connect(self.ruta_db)
        _insertar_producto(conexion, id_proveedor="1", **campos)
        conexion.commit()
        conexion.close()

    def test_reconstruye_el_producto_a_partir_del_id_de_su_url(self):
        url = "https://www.epa.co.cr/producto/cemento-gris-42-5kg"
        self._insertar(
            nombre="Cemento Gris 42.5kg", precio=5000, categoria="Construcción",
            url_producto=url, url_imagen="https://img/cemento.jpg", marca="Holcim", sku="C001",
        )

        resultado = producto_por_id(_id_de(url))

        self.assertEqual(resultado["nombre"], "Cemento Gris 42.5kg")
        self.assertEqual(resultado["precio"], 5000)
        self.assertEqual(resultado["proveedor"], "EPA")
        self.assertEqual(resultado["id_proveedor"], "1")
        self.assertEqual(resultado["url_producto"], url)
        self.assertEqual(resultado["marca"], "Holcim")

    def test_id_que_no_es_base64_valido_da_404_no_500(self):
        with self.assertRaises(HTTPException) as contexto:
            producto_por_id("esto-no-es-un-id-valido-!!!")
        self.assertEqual(contexto.exception.status_code, 404)

    def test_url_bien_formada_pero_que_no_existe_en_el_catalogo_da_404(self):
        with self.assertRaises(HTTPException) as contexto:
            producto_por_id(_id_de("https://www.epa.co.cr/producto/no-existe"))
        self.assertEqual(contexto.exception.status_code, 404)

    def test_incluye_familia_cuando_el_producto_tiene_una(self):
        conexion = sqlite3.connect(self.ruta_db)
        conexion.execute(
            "INSERT INTO familias_producto (id, proveedor, categoria, firma_base, nombre_familia) "
            "VALUES (1, 'EPA', 'Pinturas', 'pintura blanca', 'Pintura Blanca')"
        )
        conexion.commit()
        conexion.close()

        url = "https://www.epa.co.cr/producto/pintura-blanca-galon"
        self._insertar(
            nombre="Pintura Blanca Galón", categoria="Pinturas",
            url_producto=url, familia_id=1,
        )

        resultado = producto_por_id(_id_de(url))

        self.assertEqual(resultado["familia_id"], 1)
        self.assertEqual(resultado["nombre_familia"], "Pintura Blanca")

    def test_url_con_caracteres_no_ascii_hace_ida_y_vuelta(self):
        url = "https://tienda.com/prod?x=áéí&y=ñ"
        self._insertar(nombre="Producto con acentos", url_producto=url)

        resultado = producto_por_id(_id_de(url))

        self.assertEqual(resultado["url_producto"], url)


if __name__ == "__main__":
    unittest.main()
