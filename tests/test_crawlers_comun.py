"""Pruebas para los helpers genéricos agregados a crawlers/comun.py al
refactorizar los 4 proveedores para reutilizar código común (ver
ARQUITECTURA_CRAWLERS.md). test_enriquecimiento.py ya prueba que el
comportamiento existente de El Lagar y comun.py no cambió; estas pruebas
cubren la lógica *nueva* en aislamiento, con datos sintéticos, algo que
la suite existente no hacía porque esos helpers no existían antes.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import requests

from crawlers import comun


class PruebasDescargarPaginado(unittest.TestCase):
    def test_acumula_hasta_la_ultima_pagina(self):
        paginas = {
            1: (["a", "b"], False),
            2: (["c"], True),
        }

        resultado = comun.descargar_paginado(lambda pagina: paginas[pagina])

        self.assertEqual(resultado, ["a", "b", "c"])

    def test_para_en_pagina_vacia_aunque_no_diga_es_ultima(self):
        # Carbone Store no tiene forma de avisar "es la última" -- la
        # única señal real es una página vacía. Confirma que el bucle no
        # necesita ese aviso para parar.
        paginas = {
            1: (["a"], False),
            2: ([], False),
        }

        resultado = comun.descargar_paginado(lambda pagina: paginas[pagina])

        self.assertEqual(resultado, ["a"])

    def test_una_sola_pagina(self):
        resultado = comun.descargar_paginado(lambda pagina: (["único"], True))
        self.assertEqual(resultado, ["único"])

    def test_pausa_entre_paginas_no_se_aplica_en_la_ultima(self):
        """El caso real de Carbone: pausa_entre_paginas evita 429 en
        páginas intermedias, pero no tiene sentido esperar después de la
        última página real ni después de la página vacía que confirma
        que se acabó."""
        paginas = {
            1: (["a"], False),
            2: (["b"], True),
        }

        with mock.patch.object(comun.time, "sleep") as sleep_mock:
            comun.descargar_paginado(lambda pagina: paginas[pagina], pausa_entre_paginas=0.5)

        # Solo debería haber pausado una vez: después de la página 1 (no
        # última), nunca después de la página 2 (sí última).
        sleep_mock.assert_called_once_with(0.5)

    def test_sin_pausa_por_defecto(self):
        with mock.patch.object(comun.time, "sleep") as sleep_mock:
            comun.descargar_paginado(lambda pagina: (["a"], True))

        sleep_mock.assert_not_called()


class PruebasDescargarYNormalizarPorCategoria(unittest.TestCase):
    def test_normaliza_y_acumula_entre_categorias(self):
        categorias = {"Herramientas": 1, "Pinturas": 2}

        def descargar_categoria(categoria_id):
            return [{"id": categoria_id, "n": 1}, {"id": categoria_id, "n": 2}]

        def normalizar_producto(producto, nombre_categoria):
            return {"categoria": nombre_categoria, **producto}

        resultado = comun.descargar_y_normalizar_por_categoria(
            categorias, descargar_categoria, normalizar_producto
        )

        self.assertEqual(len(resultado), 4)
        self.assertEqual({r["categoria"] for r in resultado}, {"Herramientas", "Pinturas"})

    def test_una_categoria_caida_no_aborta_las_demas(self):
        categorias = {"Rota": 1, "Buena": 2}

        def descargar_categoria(categoria_id):
            if categoria_id == 1:
                raise requests.exceptions.ConnectionError("caída")
            return [{"id": categoria_id}]

        resultado = comun.descargar_y_normalizar_por_categoria(
            categorias, descargar_categoria, lambda p, c: {"categoria": c, **p}
        )

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["categoria"], "Buena")

    def test_categoria_sin_productos_no_agrega_nada(self):
        resultado = comun.descargar_y_normalizar_por_categoria(
            {"Vacía": 1}, lambda categoria_id: [], lambda p, c: p
        )
        self.assertEqual(resultado, [])


class PruebasEjecutarActualizacion(unittest.TestCase):
    def test_guarda_y_devuelve_la_cantidad(self):
        productos = [{"proveedor": "X"}]

        with mock.patch.object(comun, "guardar_productos", return_value=1) as guardar_mock:
            cantidad = comun.ejecutar_actualizacion("Proveedor X", lambda: productos)

        guardar_mock.assert_called_once_with(productos)
        self.assertEqual(cantidad, 1)

    def test_usa_la_forma_de_verbo_indicada(self):
        with mock.patch.object(comun, "guardar_productos", return_value=5):
            captura = io.StringIO()
            with redirect_stdout(captura):
                comun.ejecutar_actualizacion(
                    "Ferretería Brenes", lambda: [], forma_verbo="actualizada"
                )

        self.assertIn("Ferretería Brenes actualizada: 5 productos.", captura.getvalue())

    def test_banner_usa_el_nombre_en_mayusculas(self):
        with mock.patch.object(comun, "guardar_productos", return_value=0):
            captura = io.StringIO()
            with redirect_stdout(captura):
                comun.ejecutar_actualizacion("El Lagar", lambda: [])

        self.assertIn("=== ACTUALIZANDO EL LAGAR ===", captura.getvalue())


class PruebasCheckpointGenerico(unittest.TestCase):
    """Misma lógica que ya probaba tests/test_enriquecimiento.py contra
    los wrappers de El Lagar, pero acá directamente contra las funciones
    genéricas de comun.py -- confirma que son reutilizables para
    cualquier proveedor futuro con un `ruta` y `max_horas` propios, no
    solo para El Lagar."""

    def setUp(self):
        self.carpeta_temporal = tempfile.mkdtemp()
        self.ruta = os.path.join(self.carpeta_temporal, "checkpoint.json")

    def tearDown(self):
        if os.path.exists(self.ruta):
            os.remove(self.ruta)
        os.rmdir(self.carpeta_temporal)

    def test_guardar_y_cargar(self):
        comun.guardar_checkpoint(self.ruta, {"a", "b"})
        self.assertEqual(comun.cargar_checkpoint(self.ruta, max_horas=6), {"a", "b"})

    def test_sin_archivo_devuelve_vacio(self):
        self.assertEqual(comun.cargar_checkpoint(self.ruta, max_horas=6), set())

    def test_borrar(self):
        comun.guardar_checkpoint(self.ruta, {"a"})
        self.assertTrue(os.path.exists(self.ruta))
        comun.borrar_checkpoint(self.ruta)
        self.assertFalse(os.path.exists(self.ruta))

    def test_ruta_distinta_no_interfiere_con_otro_proveedor(self):
        """Dos proveedores usando checkpoints en rutas distintas no se
        pisan entre sí -- la función es genérica sobre `ruta`, no guarda
        estado global propio."""
        otra_ruta = os.path.join(self.carpeta_temporal, "otro_proveedor.json")
        comun.guardar_checkpoint(self.ruta, {"1"})
        comun.guardar_checkpoint(otra_ruta, {"2", "3"})

        self.assertEqual(comun.cargar_checkpoint(self.ruta, max_horas=6), {"1"})
        self.assertEqual(comun.cargar_checkpoint(otra_ruta, max_horas=6), {"2", "3"})

        os.remove(otra_ruta)


if __name__ == "__main__":
    unittest.main()
