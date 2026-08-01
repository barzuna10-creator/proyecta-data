"""Pruebas para la capa de cotización de api/repositorio_proyectos.py:
agrupación de materiales por partida, y el desglose de la cotización
(subtotal, indirectos, imprevistos, margen, total, costo por m²).

repositorio_proyectos.py no tenía ninguna prueba antes de esto -- se cubre
tanto la lógica pura (_agrupar_por_partida, _calcular_cotizacion) como el
camino completo vía la API pública del módulo, contra una base SQLite
temporal (mismo patrón que tests/test_presupuestos.py), nunca contra
database/proyecta.db.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from api.repositorio_proyectos import (
    SIN_PARTIDA,
    _agrupar_por_partida,
    _calcular_cotizacion,
    actualizar_item,
    actualizar_proyecto,
    agregar_item,
    crear_proyecto,
    eliminar_proyecto,
    listar_proyectos,
    obtener_proyecto,
)


def _item(cantidad=1, estado="pendiente", partida=None, precio_actual=None, precio_al_agregar=None):
    return {
        "cantidad": cantidad,
        "estado": estado,
        "partida": partida,
        "precio_actual": precio_actual,
        "precio_al_agregar": precio_al_agregar,
    }


class PruebaAgruparPorPartida(unittest.TestCase):
    def test_agrupa_por_partida_con_subtotal_correcto(self):
        items = [
            _item(cantidad=2, partida="Cimentación", precio_actual=1000),
            _item(cantidad=3, partida="Cimentación", precio_actual=500),
            _item(cantidad=1, partida="Acabados", precio_actual=20000),
        ]
        grupos = _agrupar_por_partida(items)

        por_nombre = {g["partida"]: g for g in grupos}
        self.assertEqual(por_nombre["Cimentación"]["subtotal"], 3500)
        self.assertEqual(len(por_nombre["Cimentación"]["items"]), 2)
        self.assertEqual(por_nombre["Acabados"]["subtotal"], 20000)

    def test_items_sin_partida_van_a_sin_partida(self):
        items = [_item(cantidad=1, partida=None, precio_actual=100)]
        grupos = _agrupar_por_partida(items)

        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]["partida"], SIN_PARTIDA)

    def test_items_descartados_se_excluyen(self):
        items = [
            _item(cantidad=1, partida="Cimentación", estado="descartado", precio_actual=1000),
            _item(cantidad=1, partida="Cimentación", estado="pendiente", precio_actual=500),
        ]
        grupos = _agrupar_por_partida(items)

        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]["subtotal"], 500)

    def test_usa_precio_al_agregar_si_no_hay_precio_actual(self):
        items = [_item(cantidad=2, partida="Eléctrico", precio_actual=None, precio_al_agregar=750)]
        grupos = _agrupar_por_partida(items)

        self.assertEqual(grupos[0]["subtotal"], 1500)

    def test_precio_totalmente_desconocido_no_lanza_y_suma_cero(self):
        items = [_item(cantidad=3, partida="Hidráulico", precio_actual=None, precio_al_agregar=None)]
        grupos = _agrupar_por_partida(items)

        self.assertEqual(grupos[0]["subtotal"], 0)

    def test_orden_sigue_secuencia_de_construccion_no_alfabetico(self):
        # Se agregan a propósito en orden inverso al de construcción, para
        # confirmar que el orden de salida no es ni de inserción ni A-Z.
        items = [
            _item(cantidad=1, partida="Acabados", precio_actual=100),
            _item(cantidad=1, partida="Cimentación", precio_actual=100),
            _item(cantidad=1, partida="Estructura", precio_actual=100),
        ]
        grupos = _agrupar_por_partida(items)
        orden = [g["partida"] for g in grupos]

        self.assertEqual(orden, ["Cimentación", "Estructura", "Acabados"])

    def test_sin_partida_siempre_queda_de_ultimo(self):
        items = [
            _item(cantidad=1, partida=None, precio_actual=100),
            _item(cantidad=1, partida="Zetas Custom", precio_actual=100),
            _item(cantidad=1, partida="Cimentación", precio_actual=100),
        ]
        grupos = _agrupar_por_partida(items)
        orden = [g["partida"] for g in grupos]

        self.assertEqual(orden[-1], SIN_PARTIDA)
        self.assertEqual(orden[0], "Cimentación")

    def test_lista_vacia_sin_items(self):
        self.assertEqual(_agrupar_por_partida([]), [])


class PruebaCalcularCotizacion(unittest.TestCase):
    def _proyecto(self, **overrides):
        base = {
            "indirectos_porcentaje": 0, "imprevistos_porcentaje": 0,
            "margen_porcentaje": 0, "area_m2": None,
        }
        base.update(overrides)
        return base

    def test_sin_porcentajes_total_es_igual_al_subtotal(self):
        items = [_item(cantidad=1, partida="Estructura", precio_actual=10000)]
        cot = _calcular_cotizacion(self._proyecto(), items)

        self.assertEqual(cot["subtotal_materiales"], 10000)
        self.assertEqual(cot["indirectos"], 0)
        self.assertEqual(cot["imprevistos"], 0)
        self.assertEqual(cot["margen"], 0)
        self.assertEqual(cot["total_final"], 10000)

    def test_porcentajes_se_aplican_planos_sobre_el_mismo_subtotal(self):
        # No en cascada: los tres se calculan sobre subtotal_materiales,
        # no uno encima del resultado del anterior -- ver el comentario de
        # diseño en _calcular_cotizacion.
        items = [_item(cantidad=1, partida="Estructura", precio_actual=100000)]
        proyecto = self._proyecto(
            indirectos_porcentaje=10, imprevistos_porcentaje=5, margen_porcentaje=20
        )
        cot = _calcular_cotizacion(proyecto, items)

        self.assertEqual(cot["indirectos"], 10000)
        self.assertEqual(cot["imprevistos"], 5000)
        self.assertEqual(cot["margen"], 20000)
        self.assertEqual(cot["total_final"], 135000)

    def test_costo_por_m2_cuando_hay_area(self):
        items = [_item(cantidad=1, partida="Estructura", precio_actual=90000)]
        proyecto = self._proyecto(area_m2=30)
        cot = _calcular_cotizacion(proyecto, items)

        self.assertEqual(cot["costo_por_m2"], 3000)

    def test_costo_por_m2_none_sin_area(self):
        items = [_item(cantidad=1, partida="Estructura", precio_actual=90000)]
        cot = _calcular_cotizacion(self._proyecto(area_m2=None), items)

        self.assertIsNone(cot["costo_por_m2"])

    def test_proyecto_sin_items_da_totales_en_cero(self):
        cot = _calcular_cotizacion(self._proyecto(margen_porcentaje=15), [])

        self.assertEqual(cot["partidas"], [])
        self.assertEqual(cot["subtotal_materiales"], 0)
        self.assertEqual(cot["total_final"], 0)


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
        CREATE TABLE proyectos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            propietario_id TEXT NOT NULL,
            nombre TEXT NOT NULL,
            comentario TEXT,
            estado TEXT NOT NULL DEFAULT 'activo',
            fecha_objetivo TEXT,
            token_compartido TEXT UNIQUE NOT NULL,
            fecha_creacion TEXT,
            fecha_actualizacion TEXT,
            cliente TEXT,
            direccion TEXT,
            area_m2 REAL,
            indirectos_porcentaje REAL NOT NULL DEFAULT 0,
            imprevistos_porcentaje REAL NOT NULL DEFAULT 0,
            margen_porcentaje REAL NOT NULL DEFAULT 0
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE items_proyecto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proyecto_id INTEGER NOT NULL REFERENCES proyectos(id) ON DELETE CASCADE,
            proveedor TEXT NOT NULL,
            id_proveedor TEXT NOT NULL,
            cantidad REAL NOT NULL DEFAULT 1,
            unidad_medida TEXT,
            estado TEXT NOT NULL DEFAULT 'pendiente',
            prioridad TEXT,
            comentario TEXT,
            nombre_al_agregar TEXT NOT NULL,
            marca_al_agregar TEXT,
            categoria_al_agregar TEXT,
            precio_al_agregar REAL,
            url_imagen_al_agregar TEXT,
            url_producto_al_agregar TEXT,
            fecha_agregado TEXT,
            partida TEXT,
            UNIQUE(proyecto_id, proveedor, id_proveedor)
        )
        """
    )
    conexion.commit()
    conexion.close()
    return archivo.name


def _insertar_producto(conexion, **campos):
    base = {
        "proveedor": None, "id_proveedor": None, "sku": None, "nombre": None,
        "marca": None, "categoria": None, "subcategoria": None, "precio": 1000,
        "descripcion": None, "url_imagen": None, "url_producto": None,
        "peso": None, "imagenes_adicionales": None, "familia_id": None,
    }
    base.update(campos)
    columnas = ", ".join(base.keys())
    marcadores = ", ".join("?" for _ in base)
    conexion.execute(
        f"INSERT INTO productos ({columnas}) VALUES ({marcadores})", list(base.values())
    )


class BasePruebaIntegracion(unittest.TestCase):
    PROPIETARIO = "propietario-cotizacion-test"

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

    def _insertar_productos(self, productos):
        conexion = sqlite3.connect(self.ruta_db)
        for fila in productos:
            _insertar_producto(conexion, **fila)
        conexion.commit()
        conexion.close()


class PruebaFlujoCompletoCotizacion(BasePruebaIntegracion):
    def test_ficha_y_partidas_de_extremo_a_extremo(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg",
             "categoria": "Construcción", "precio": 5000},
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Cerámica 60x60",
             "categoria": "Acabados", "precio": 8000},
        ])

        proyecto = crear_proyecto(self.PROPIETARIO, "Casa Pérez")
        pid = proyecto["id"]

        agregar_item(pid, self.PROPIETARIO, "EPA", "1", 10)
        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "2", 5)
        items = proyecto["items"]

        actualizar_item(pid, self.PROPIETARIO, items[0]["id"], {"partida": "Cimentación"})
        proyecto = actualizar_item(pid, self.PROPIETARIO, items[1]["id"], {"partida": "Acabados"})

        proyecto = actualizar_proyecto(pid, self.PROPIETARIO, {
            "cliente": "Juan Pérez",
            "direccion": "San José",
            "area_m2": 50,
            "indirectos_porcentaje": 10,
            "imprevistos_porcentaje": 5,
            "margen_porcentaje": 15,
        })

        self.assertEqual(proyecto["cliente"], "Juan Pérez")
        self.assertEqual(proyecto["direccion"], "San José")
        self.assertEqual(proyecto["area_m2"], 50)

        cot = proyecto["cotizacion"]
        self.assertEqual(cot["subtotal_materiales"], 90000)  # 10*5000 + 5*8000
        self.assertEqual(cot["indirectos"], 9000)
        self.assertEqual(cot["imprevistos"], 4500)
        self.assertEqual(cot["margen"], 13500)
        self.assertEqual(cot["total_final"], 117000)
        self.assertEqual(cot["costo_por_m2"], 2340)
        self.assertEqual([p["partida"] for p in cot["partidas"]], ["Cimentación", "Acabados"])

        eliminar_proyecto(pid, self.PROPIETARIO)

    def test_proyecto_recien_creado_tiene_cotizacion_vacia(self):
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto nuevo")

        self.assertEqual(proyecto["cotizacion"]["subtotal_materiales"], 0)
        self.assertEqual(proyecto["cotizacion"]["total_final"], 0)
        self.assertEqual(proyecto["cotizacion"]["partidas"], [])
        self.assertEqual(proyecto["indirectos_porcentaje"], 0)
        self.assertIsNone(proyecto["cliente"])

        eliminar_proyecto(proyecto["id"], self.PROPIETARIO)

    def test_item_descartado_no_afecta_la_cotizacion(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Varilla #4",
             "categoria": "Construcción", "precio": 3000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con descarte")
        pid = proyecto["id"]

        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 2)
        item_id = proyecto["items"][0]["id"]
        proyecto = actualizar_item(pid, self.PROPIETARIO, item_id, {"estado": "descartado"})

        self.assertEqual(proyecto["cotizacion"]["subtotal_materiales"], 0)
        self.assertEqual(proyecto["cotizacion"]["partidas"], [])

        eliminar_proyecto(pid, self.PROPIETARIO)

    def test_partida_es_texto_libre_sin_restriccion(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Producto genérico",
             "categoria": "General", "precio": 1000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con partida custom")
        pid = proyecto["id"]

        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 1)
        item_id = proyecto["items"][0]["id"]
        proyecto = actualizar_item(pid, self.PROPIETARIO, item_id, {"partida": "Trabajo de jardinería"})

        self.assertEqual(proyecto["cotizacion"]["partidas"][0]["partida"], "Trabajo de jardinería")

        eliminar_proyecto(pid, self.PROPIETARIO)

    def test_listar_proyectos_incluye_cliente_sin_romper_totales(self):
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto listado")
        actualizar_proyecto(proyecto["id"], self.PROPIETARIO, {"cliente": "María Rodríguez"})

        resumenes = listar_proyectos(self.PROPIETARIO)
        resumen = next(r for r in resumenes if r["id"] == proyecto["id"])

        self.assertEqual(resumen["cliente"], "María Rodríguez")
        self.assertEqual(resumen["total_pendiente"], 0)
        self.assertEqual(resumen["cantidad_items"], 0)

        eliminar_proyecto(proyecto["id"], self.PROPIETARIO)

    def test_proyecto_de_otro_propietario_no_se_puede_editar(self):
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto privado")

        resultado = actualizar_proyecto(
            proyecto["id"], "otro-propietario", {"cliente": "Intento ajeno"}
        )

        self.assertIsNone(resultado)

        eliminar_proyecto(proyecto["id"], self.PROPIETARIO)


if __name__ == "__main__":
    unittest.main()
