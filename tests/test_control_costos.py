"""Pruebas para Control de Costos (ver CONTROL_DE_COSTOS.md):
congelar_presupuesto/obtener_control_costos en api/repositorio_proyectos.py.

Mismo patrón de base de datos temporal que tests/test_repositorio_proyectos.py
-- reutiliza su BasePruebaIntegracion (incluye ya la tabla
presupuesto_congelado) en vez de duplicar el helper."""

import unittest

from api.repositorio_proyectos import (
    actualizar_item,
    actualizar_proyecto,
    agregar_item,
    congelar_presupuesto,
    crear_proyecto,
    obtener_control_costos,
)
from tests.test_repositorio_proyectos import BasePruebaIntegracion


class PruebaSinLineaBase(BasePruebaIntegracion):
    def test_sin_congelar_nunca_no_inventa_una_linea_base(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg", "precio": 5000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Casa sin aprobar")
        agregar_item(proyecto["id"], self.PROPIETARIO, "EPA", "1", 10)

        resultado = obtener_control_costos(proyecto["id"], self.PROPIETARIO)

        self.assertFalse(resultado["tiene_linea_base"])
        self.assertIsNone(resultado["linea_base"])
        self.assertIsNone(resultado["saldo_disponible"])
        self.assertIsNone(resultado["porcentaje_ejecutado"])
        self.assertIsNone(resultado["estado"])
        self.assertEqual(resultado["gasto_real"], 0)  # nada marcado "comprado" todavía

    def test_proyecto_inexistente_devuelve_none(self):
        self.assertIsNone(obtener_control_costos(999999, self.PROPIETARIO))
        self.assertIsNone(congelar_presupuesto(999999, self.PROPIETARIO))

    def test_proyecto_de_otro_propietario_devuelve_none(self):
        proyecto = crear_proyecto(self.PROPIETARIO, "Casa ajena")
        self.assertIsNone(obtener_control_costos(proyecto["id"], "otro-propietario"))
        self.assertIsNone(congelar_presupuesto(proyecto["id"], "otro-propietario"))


class PruebaCongelarPresupuesto(BasePruebaIntegracion):
    def _proyecto_con_cotizacion(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg", "precio": 5000},
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Cerámica 60x60", "precio": 8000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Casa Pérez")
        pid = proyecto["id"]
        agregar_item(pid, self.PROPIETARIO, "EPA", "1", 10)  # 50,000
        agregar_item(pid, self.PROPIETARIO, "EPA", "2", 5)  # 40,000
        actualizar_proyecto(pid, self.PROPIETARIO, {
            "indirectos_porcentaje": 10, "imprevistos_porcentaje": 5, "margen_porcentaje": 15,
        })
        self._confirmar_presentaciones_unitarias(pid)
        return pid

    def test_congela_los_totales_exactos_de_la_cotizacion_actual(self):
        pid = self._proyecto_con_cotizacion()

        resultado = congelar_presupuesto(pid, self.PROPIETARIO)

        self.assertTrue(resultado["tiene_linea_base"])
        base = resultado["linea_base"]
        self.assertEqual(base["subtotal_materiales"], 90000)
        self.assertEqual(base["indirectos"], 9000)
        self.assertEqual(base["imprevistos"], 4500)
        self.assertEqual(base["margen"], 13500)
        self.assertEqual(base["total_final"], 117000)

    def test_snapshot_de_partidas_no_incluye_campos_pesados(self):
        pid = self._proyecto_con_cotizacion()

        resultado = congelar_presupuesto(pid, self.PROPIETARIO)

        partidas = resultado["linea_base"]["partidas"]
        self.assertTrue(partidas)
        item = partidas[0]["items"][0]
        self.assertEqual(
            set(item.keys()),
            {"nombre", "cantidad", "precio_unitario", "calculo_compra"},
        )

    def test_congelar_dos_veces_no_borra_la_linea_base_anterior(self):
        pid = self._proyecto_con_cotizacion()

        primera = congelar_presupuesto(pid, self.PROPIETARIO)
        # cambia el alcance antes de re-aprobar
        agregar_item(pid, self.PROPIETARIO, "EPA", "1", 1)
        segunda = congelar_presupuesto(pid, self.PROPIETARIO)

        self.assertNotEqual(primera["linea_base"]["total_final"], segunda["linea_base"]["total_final"])
        # obtener_control_costos siempre usa la MÁS RECIENTE
        actual = obtener_control_costos(pid, self.PROPIETARIO)
        self.assertEqual(actual["linea_base"]["total_final"], segunda["linea_base"]["total_final"])

        import sqlite3
        conexion = sqlite3.connect(self.ruta_db)
        total_filas = conexion.execute("SELECT COUNT(*) FROM presupuesto_congelado").fetchone()[0]
        conexion.close()
        self.assertEqual(total_filas, 2)  # ninguna se sobreescribió


class PruebaComparacionContraGastoReal(BasePruebaIntegracion):
    def _proyecto_congelado(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg", "precio": 10000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Casa control costos")
        pid = proyecto["id"]
        # sin indirectos/imprevistos/margen -- total_final = subtotal = 100,000, para que
        # el gasto real se pueda comparar contra un número redondo sin ruido de porcentajes
        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 10)
        item_id = proyecto["items"][0]["id"]
        self._confirmar_presentaciones_unitarias(pid)
        congelar_presupuesto(pid, self.PROPIETARIO)
        return pid, item_id

    def test_estado_en_curso_cuando_el_gasto_no_alcanza_el_presupuesto(self):
        pid, item_id = self._proyecto_congelado()
        actualizar_item(pid, self.PROPIETARIO, item_id, {"cantidad": 3})  # 30,000 de 100,000
        actualizar_item(pid, self.PROPIETARIO, item_id, {"estado": "comprado"})

        resultado = obtener_control_costos(pid, self.PROPIETARIO)

        self.assertEqual(resultado["gasto_real"], 30000)
        self.assertEqual(resultado["saldo_disponible"], 70000)
        self.assertEqual(resultado["porcentaje_ejecutado"], 30.0)
        self.assertEqual(resultado["estado"], "en_curso")

    def test_estado_por_agotarse_desde_90_por_ciento(self):
        pid, item_id = self._proyecto_congelado()
        actualizar_item(pid, self.PROPIETARIO, item_id, {"cantidad": 9})  # 90,000 de 100,000
        actualizar_item(pid, self.PROPIETARIO, item_id, {"estado": "comprado"})

        resultado = obtener_control_costos(pid, self.PROPIETARIO)

        self.assertEqual(resultado["porcentaje_ejecutado"], 90.0)
        self.assertEqual(resultado["estado"], "por_agotarse")

    def test_estado_excedido_cuando_el_gasto_supera_el_presupuesto(self):
        pid, item_id = self._proyecto_congelado()
        actualizar_item(pid, self.PROPIETARIO, item_id, {"cantidad": 15})  # 150,000 de 100,000
        actualizar_item(pid, self.PROPIETARIO, item_id, {"estado": "comprado"})

        resultado = obtener_control_costos(pid, self.PROPIETARIO)

        self.assertEqual(resultado["saldo_disponible"], -50000)
        self.assertEqual(resultado["estado"], "excedido")

    def test_items_descartados_no_cuentan_como_gasto(self):
        pid, item_id = self._proyecto_congelado()
        actualizar_item(pid, self.PROPIETARIO, item_id, {"estado": "descartado"})

        resultado = obtener_control_costos(pid, self.PROPIETARIO)

        self.assertEqual(resultado["gasto_real"], 0)
        self.assertEqual(resultado["estado"], "en_curso")

    def test_item_nuevo_agregado_despues_de_congelar_si_cuenta_como_gasto(self):
        """El gasto real siempre refleja el estado ACTUAL del proyecto, no
        una foto -- solo la columna "presupuestado" queda congelada."""
        pid, item_id = self._proyecto_congelado()
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Extra no presupuestado", "precio": 20000},
        ])
        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "2", 1)
        item_extra_id = [i for i in proyecto["items"] if i["proveedor"] == "EPA" and i["id_proveedor"] == "2"][0]["id"]
        actualizar_item(pid, self.PROPIETARIO, item_extra_id, {"estado": "comprado"})

        resultado = obtener_control_costos(pid, self.PROPIETARIO)

        self.assertEqual(resultado["gasto_real"], 20000)
        self.assertEqual(resultado["linea_base"]["total_final"], 100000)  # la línea base no cambió


if __name__ == "__main__":
    unittest.main()
