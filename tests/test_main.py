"""Pruebas para el endpoint GET /productos/{id} (api/main.py::producto_por_id)
-- RELEASE_CANDIDATE.md: reconstruye un producto desde el backend a partir
del id que ya usa /producto/{id} en el frontend (base64url de
url_producto, ver id_producto.py), para que un link compartido,
recargado, o sin sessionStorage siga funcionando.

Mismo patrón que tests/test_routers_proyectos.py: la función de la ruta
es una función Python normal (sin `async def`, sin depender de un
Request real), así que se llama directo -- no hace falta un TestClient
ni la dependencia de httpx."""

import base64
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from fastapi import HTTPException

from api.main import producto_por_id


def _id_de(url):
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def _crear_db_temporal():
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
        self.ruta_db = _crear_db_temporal()
        import db
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
