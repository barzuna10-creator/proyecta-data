"""Pruebas específicas para el endurecimiento del enriquecimiento de
catálogo: reconstrucción del índice FTS5, limpieza de HTML/CSS embebido,
reintentos de red, checkpoint incremental de El Lagar, y campos opcionales
en la API. Usa unittest + unittest.mock (stdlib) -- el proyecto no usa
pytest en ningún otro lado, así que no se agrega esa dependencia.

No toca la base de datos real: cada prueba que necesita SQLite crea una
temporal y apunta `db.BASE_DATOS` ahí con monkeypatch.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import requests


def _crear_db_temporal():
    """Esquema mínimo (productos + productos_fts) igual al real, en un
    archivo temporal, para no tocar database/proyecta.db en las pruebas."""

    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    conexion = sqlite3.connect(archivo.name)
    conexion.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor TEXT,
            id_proveedor TEXT,
            sku TEXT,
            nombre TEXT,
            marca TEXT,
            categoria TEXT,
            subcategoria TEXT,
            precio REAL,
            iva REAL,
            cabys TEXT,
            descripcion TEXT,
            url_imagen TEXT,
            url_producto TEXT,
            compra_online INTEGER,
            fecha_actualizacion TEXT,
            familia_id INTEGER,
            peso TEXT,
            imagenes_adicionales TEXT,
            UNIQUE(proveedor, id_proveedor)
        )
        """
    )
    conexion.execute(
        "CREATE VIRTUAL TABLE productos_fts USING fts5(nombre, categoria, subcategoria, content='')"
    )
    conexion.commit()
    conexion.close()

    return archivo.name


class PruebasReconstruirIndice(unittest.TestCase):
    """El bug real: reconstruir_indice() usaba `DELETE FROM productos_fts`,
    que SQLite rechaza en una tabla FTS5 contentless. Esta prueba habría
    fallado con el código viejo y confirma que el fix ('delete-all') sigue
    funcionando, incluso llamado dos veces seguidas."""

    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        conexion = sqlite3.connect(self.ruta_db)
        conexion.executemany(
            "INSERT INTO productos (proveedor, id_proveedor, nombre, categoria, subcategoria, precio) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("EPA", "1", "Taladro percutor", "Herramientas", "Taladros", 1000),
                ("Carbone Store", "2", "Escalera de aluminio", "Escaleras", None, 2000),
                ("El Lagar", "3", "Pintura blanca", "Pinturas", "Interiores", 3000),
            ],
        )
        conexion.commit()
        conexion.close()

    def tearDown(self):
        os.remove(self.ruta_db)

    def test_reconstruir_indice_no_lanza_y_indexa_todo(self):
        import db
        import busqueda

        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            n = busqueda.reconstruir_indice()
            self.assertEqual(n, 3)

            conexion = sqlite3.connect(self.ruta_db)
            total = conexion.execute("SELECT COUNT(*) FROM productos_fts").fetchone()[0]
            conexion.close()
            self.assertEqual(total, 3)

    def test_reconstruir_indice_es_repetible(self):
        """Correrlo dos veces seguidas (como pasaría si main.py se corre
        varias veces) no debe fallar ni duplicar filas en el índice."""
        import db
        import busqueda

        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            busqueda.reconstruir_indice()
            n_segunda = busqueda.reconstruir_indice()
            self.assertEqual(n_segunda, 3)

            conexion = sqlite3.connect(self.ruta_db)
            total = conexion.execute("SELECT COUNT(*) FROM productos_fts").fetchone()[0]
            conexion.close()
            self.assertEqual(total, 3)


class PruebasLimpiarHtml(unittest.TestCase):
    """Caso real encontrado en Carbone Store: descripciones con tablas de
    'Ficha Técnica' que traen su propio <style> inline -- la primera
    versión del limpiador dejaba pasar ese CSS como si fuera texto."""

    def test_quita_css_de_bloques_style(self):
        from crawlers.comun import limpiar_html

        html = (
            "<style>.tg{border-collapse:collapse;border-spacing:0;}"
            ".tg td{border-style:solid;border-width:1px;}</style>"
            "<p>Escalera de Aluminio HOTECHE</p>"
            "<table><tr><td>Marca</td><td>HOTECHE</td></tr></table>"
        )

        resultado = limpiar_html(html)

        self.assertNotIn("border-collapse", resultado)
        self.assertNotIn("<style", resultado)
        self.assertIn("Escalera de Aluminio HOTECHE", resultado)
        self.assertIn("Marca", resultado)
        self.assertIn("HOTECHE", resultado)

    def test_quita_script(self):
        from crawlers.comun import limpiar_html

        html = "<p>Producto real</p><script>alert('x')</script>"
        resultado = limpiar_html(html)

        self.assertNotIn("alert", resultado)
        self.assertIn("Producto real", resultado)

    def test_html_vacio_devuelve_none(self):
        from crawlers.comun import limpiar_html

        self.assertIsNone(limpiar_html(None))
        self.assertIsNone(limpiar_html(""))
        self.assertIsNone(limpiar_html("   "))


class PruebasReintentos(unittest.TestCase):
    """pedir_con_reintentos() -- agregado tras una descarga completa de
    Carbone Store que se perdía entera por un ConnectionResetError a mitad
    de camino."""

    def test_reintenta_y_eventualmente_funciona(self):
        from crawlers.comun import pedir_con_reintentos

        llamada = mock.Mock(
            side_effect=[
                requests.exceptions.ConnectionError("reset"),
                requests.exceptions.ConnectionError("reset otra vez"),
                "respuesta-ok",
            ]
        )

        resultado = pedir_con_reintentos(llamada, reintentos=3, espera_base=0.01)

        self.assertEqual(resultado, "respuesta-ok")
        self.assertEqual(llamada.call_count, 3)

    def test_agota_reintentos_y_relanza(self):
        from crawlers.comun import pedir_con_reintentos

        llamada = mock.Mock(side_effect=requests.exceptions.Timeout("nunca responde"))

        with self.assertRaises(requests.exceptions.Timeout):
            pedir_con_reintentos(llamada, reintentos=2, espera_base=0.01)

        self.assertEqual(llamada.call_count, 2)

    def test_no_reintenta_si_funciona_a_la_primera(self):
        from crawlers.comun import pedir_con_reintentos

        llamada = mock.Mock(return_value="ok")
        resultado = pedir_con_reintentos(llamada, reintentos=3, espera_base=0.01)

        self.assertEqual(resultado, "ok")
        self.assertEqual(llamada.call_count, 1)


class PruebasCheckpointElLagar(unittest.TestCase):
    """El checkpoint incremental: si el proceso se corta a mitad de la
    corrida (pasó en la práctica -- 7 horas colgado por una laptop
    suspendida), la siguiente corrida debe retomar donde quedó."""

    def setUp(self):
        self.carpeta_temporal = tempfile.mkdtemp()
        self.ruta_checkpoint = os.path.join(self.carpeta_temporal, "checkpoint.json")

    def tearDown(self):
        if os.path.exists(self.ruta_checkpoint):
            os.remove(self.ruta_checkpoint)
        os.rmdir(self.carpeta_temporal)

    def test_guardar_y_cargar_checkpoint(self):
        from crawlers import ellagar

        with mock.patch.object(ellagar, "CHECKPOINT_PATH", self.ruta_checkpoint):
            ellagar._guardar_checkpoint({"1", "2", "3"})
            cargado = ellagar._cargar_checkpoint()

        self.assertEqual(cargado, {"1", "2", "3"})

    def test_sin_checkpoint_devuelve_vacio(self):
        from crawlers import ellagar

        with mock.patch.object(ellagar, "CHECKPOINT_PATH", self.ruta_checkpoint):
            cargado = ellagar._cargar_checkpoint()

        self.assertEqual(cargado, set())

    def test_checkpoint_viejo_se_descarta(self):
        from crawlers import ellagar

        viejo = datetime.now() - timedelta(hours=ellagar.CHECKPOINT_MAX_HORAS + 1)
        with open(self.ruta_checkpoint, "w", encoding="utf-8") as archivo:
            json.dump({"guardado_en": viejo.isoformat(), "ids_completados": ["1", "2"]}, archivo)

        with mock.patch.object(ellagar, "CHECKPOINT_PATH", self.ruta_checkpoint):
            cargado = ellagar._cargar_checkpoint()

        self.assertEqual(cargado, set())

    def test_borrar_checkpoint(self):
        from crawlers import ellagar

        with mock.patch.object(ellagar, "CHECKPOINT_PATH", self.ruta_checkpoint):
            ellagar._guardar_checkpoint({"1"})
            self.assertTrue(os.path.exists(self.ruta_checkpoint))
            ellagar._borrar_checkpoint()
            self.assertFalse(os.path.exists(self.ruta_checkpoint))

    def test_actualizar_omite_ids_ya_completados(self):
        """El caso central: si un producto ya está en el checkpoint,
        obtener_detalle() no se debe volver a llamar para él."""
        from crawlers import ellagar

        productos_falsos = [
            {"ID": 1, "Nombre": "Producto uno", "Categoria": {}, "SubCategoria": {}, "Marca": {}},
            {"ID": 2, "Nombre": "Producto dos", "Categoria": {}, "SubCategoria": {}, "Marca": {}},
        ]

        with mock.patch.object(ellagar, "CHECKPOINT_PATH", self.ruta_checkpoint), mock.patch.object(
            ellagar, "CATEGORIAS", {"Herramientas": 7}
        ), mock.patch.object(
            ellagar, "descargar_categoria", return_value=productos_falsos
        ), mock.patch.object(
            ellagar, "guardar_productos", return_value=2
        ), mock.patch.object(
            ellagar, "obtener_detalle"
        ) as detalle_mock, mock.patch.object(
            ellagar.time, "sleep"
        ):
            detalle_mock.return_value = {
                "marca": "MARCA X",
                "descripcion": "desc",
                "imagenes_adicionales": None,
            }

            # Primera corrida: enriquece ambos productos.
            ellagar.actualizar(con_detalle=True)
            self.assertEqual(detalle_mock.call_count, 2)

            # Checkpoint se borró al completar -- para probar el resume real
            # hay que simular que quedó un checkpoint de una corrida cortada.
            ellagar._guardar_checkpoint({"1"})
            detalle_mock.reset_mock()

            ellagar.actualizar(con_detalle=True)

            # Solo el producto 2 debería haber pedido detalle de nuevo.
            ids_pedidos = [llamada.args[0] for llamada in detalle_mock.call_args_list]
            self.assertEqual(ids_pedidos, ["2"])


class PruebasCamposOpcionalesAPI(unittest.TestCase):
    """api/main.py debe omitir marca/sku/subcategoria/descripcion/peso/
    imagenes_adicionales cuando vienen NULL, en vez de mandar el campo
    vacío al frontend."""

    def _fila(self, **overrides):
        base = {
            "nombre": "Producto de prueba",
            "precio": 1000,
            "categoria": "Herramientas",
            "proveedor": "EPA",
            "id_proveedor": "1",
            "url_producto": "http://example.com",
            "url_imagen": "http://example.com/img.png",
            "marca": None,
            "sku": None,
            "subcategoria": None,
            "descripcion": None,
            "peso": None,
            "imagenes_adicionales": None,
            "familia_id": None,
            "nombre_familia": None,
        }
        base.update(overrides)
        return base

    def test_omite_campos_ausentes(self):
        import api.main as api_main

        fila = self._fila()

        with mock.patch.object(api_main, "_buscar_fts_motor", return_value=[fila]), mock.patch.object(
            api_main, "_reordenar_resultados", return_value=[fila]
        ):
            resultado = api_main._buscar_fts("taladro")

        producto = resultado[0]
        for campo in ("marca", "sku", "subcategoria", "descripcion", "peso", "imagenes_adicionales"):
            self.assertNotIn(campo, producto)

    def test_incluye_campos_presentes(self):
        import api.main as api_main

        fila = self._fila(
            marca="EINHELL",
            sku="ABC123",
            subcategoria="Taladros",
            descripcion="Un taladro real",
            peso="1500",
            imagenes_adicionales=json.dumps(["http://example.com/2.png"]),
        )

        with mock.patch.object(api_main, "_buscar_fts_motor", return_value=[fila]), mock.patch.object(
            api_main, "_reordenar_resultados", return_value=[fila]
        ):
            resultado = api_main._buscar_fts("taladro")

        producto = resultado[0]
        self.assertEqual(producto["marca"], "EINHELL")
        self.assertEqual(producto["sku"], "ABC123")
        self.assertEqual(producto["subcategoria"], "Taladros")
        self.assertEqual(producto["descripcion"], "Un taladro real")
        self.assertEqual(producto["peso"], "1500")
        self.assertEqual(producto["imagenes_adicionales"], ["http://example.com/2.png"])


if __name__ == "__main__":
    unittest.main()
