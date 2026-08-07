"""Pruebas para el Flujo de Compras (ver COMPRAS.md):
_agrupar_por_proveedor/obtener_compras/generar_orden_compra/
registrar_compra_item en api/repositorio_proyectos.py, y la
sincronización del selector rápido de estado en actualizar_item.

Mismo patrón de base de datos temporal que tests/test_control_costos.py
-- reutiliza BasePruebaIntegracion (incluye ya las tablas ordenes_compra
y las columnas nuevas de items_proyecto)."""

import unittest

from api.repositorio_proyectos import (
    actualizar_item,
    agregar_item,
    congelar_presupuesto,
    crear_proyecto,
    generar_orden_compra,
    obtener_compras,
    obtener_control_costos,
    registrar_compra_item,
)
from tests.test_repositorio_proyectos import BasePruebaIntegracion


class PruebaAgruparPorProveedor(BasePruebaIntegracion):
    def _proyecto_con_dos_proveedores(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg", "precio": 5000},
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Varilla #4", "precio": 8000},
            {"proveedor": "Brenes", "id_proveedor": "9", "nombre": "Cerámica 60x60", "precio": 9000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Casa compras")
        pid = proyecto["id"]
        agregar_item(pid, self.PROPIETARIO, "EPA", "1", 10)
        agregar_item(pid, self.PROPIETARIO, "EPA", "2", 5)
        proyecto = agregar_item(pid, self.PROPIETARIO, "Brenes", "9", 3)
        return pid, proyecto["items"]

    def test_agrupa_por_proveedor_con_subtotal_correcto(self):
        pid, _ = self._proyecto_con_dos_proveedores()
        resultado = obtener_compras(pid, self.PROPIETARIO)

        proveedores = {g["proveedor"] for g in resultado["pendientes_por_proveedor"]}
        self.assertEqual(proveedores, {"EPA", "Brenes"})

        grupo_epa = next(g for g in resultado["pendientes_por_proveedor"] if g["proveedor"] == "EPA")
        self.assertEqual(len(grupo_epa["items"]), 2)
        self.assertEqual(grupo_epa["subtotal"], 90000)  # 10*5000 + 5*8000

    def test_item_completamente_comprado_no_aparece_en_el_agrupado(self):
        pid, items = self._proyecto_con_dos_proveedores()
        item_cemento = next(i for i in items if i["id_proveedor"] == "1")
        registrar_compra_item(pid, self.PROPIETARIO, item_cemento["id"], 10)  # compra completa

        resultado = obtener_compras(pid, self.PROPIETARIO)
        grupo_epa = next(g for g in resultado["pendientes_por_proveedor"] if g["proveedor"] == "EPA")
        self.assertEqual(len(grupo_epa["items"]), 1)  # solo queda la varilla

    def test_item_descartado_no_aparece(self):
        pid, items = self._proyecto_con_dos_proveedores()
        item_cemento = next(i for i in items if i["id_proveedor"] == "1")
        actualizar_item(pid, self.PROPIETARIO, item_cemento["id"], {"estado": "descartado"})

        resultado = obtener_compras(pid, self.PROPIETARIO)
        grupo_epa = next(g for g in resultado["pendientes_por_proveedor"] if g["proveedor"] == "EPA")
        self.assertEqual(len(grupo_epa["items"]), 1)

    def test_subtotal_de_item_parcial_usa_solo_lo_pendiente(self):
        pid, items = self._proyecto_con_dos_proveedores()
        item_cemento = next(i for i in items if i["id_proveedor"] == "1")
        registrar_compra_item(pid, self.PROPIETARIO, item_cemento["id"], 4)  # 4 de 10

        resultado = obtener_compras(pid, self.PROPIETARIO)
        grupo_epa = next(g for g in resultado["pendientes_por_proveedor"] if g["proveedor"] == "EPA")
        # cemento: 6 pendientes * 5000 = 30,000; varilla: 5*8000 = 40,000
        self.assertEqual(grupo_epa["subtotal"], 70000)

    def test_proyecto_inexistente_devuelve_none(self):
        self.assertIsNone(obtener_compras(999999, self.PROPIETARIO))


class PruebaGenerarOrdenCompra(BasePruebaIntegracion):
    def _proyecto_con_dos_proveedores(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg", "precio": 5000},
            {"proveedor": "Brenes", "id_proveedor": "9", "nombre": "Cerámica 60x60", "precio": 9000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Casa OC")
        pid = proyecto["id"]
        agregar_item(pid, self.PROPIETARIO, "EPA", "1", 10)
        agregar_item(pid, self.PROPIETARIO, "Brenes", "9", 3)
        return pid

    def test_genera_orden_con_numero_y_monto_correctos(self):
        pid = self._proyecto_con_dos_proveedores()

        resultado = generar_orden_compra(pid, self.PROPIETARIO, "EPA")

        ordenes = resultado["ordenes_generadas"]
        self.assertEqual(len(ordenes), 1)
        self.assertEqual(ordenes[0]["numero"], f"OC-{pid}-1")
        self.assertEqual(ordenes[0]["proveedor"], "EPA")
        self.assertEqual(ordenes[0]["monto_total"], 50000)
        self.assertEqual(len(ordenes[0]["items"]), 1)

    def test_numeracion_consecutiva_entre_proveedores(self):
        pid = self._proyecto_con_dos_proveedores()
        generar_orden_compra(pid, self.PROPIETARIO, "EPA")
        resultado = generar_orden_compra(pid, self.PROPIETARIO, "Brenes")

        numeros = sorted(o["numero"] for o in resultado["ordenes_generadas"])
        self.assertEqual(numeros, [f"OC-{pid}-1", f"OC-{pid}-2"])

    def test_generar_no_cambia_el_estado_de_los_items(self):
        pid = self._proyecto_con_dos_proveedores()
        generar_orden_compra(pid, self.PROPIETARIO, "EPA")

        compras = obtener_compras(pid, self.PROPIETARIO)
        grupo_epa = next(g for g in compras["pendientes_por_proveedor"] if g["proveedor"] == "EPA")
        self.assertEqual(len(grupo_epa["items"]), 1)  # sigue pendiente, la orden no lo marcó comprado

    def test_proveedor_sin_pendientes_lanza_value_error(self):
        pid = self._proyecto_con_dos_proveedores()
        with self.assertRaises(ValueError):
            generar_orden_compra(pid, self.PROPIETARIO, "Carbone Store")

    def test_proyecto_inexistente_devuelve_none(self):
        self.assertIsNone(generar_orden_compra(999999, self.PROPIETARIO, "EPA"))


class PruebaRegistrarCompraItem(BasePruebaIntegracion):
    def _item_de_prueba(self, precio=5000, cantidad=10):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg", "precio": precio},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Casa registrar compra")
        pid = proyecto["id"]
        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", cantidad)
        return pid, proyecto["items"][0]["id"]

    def test_compra_total_pasa_a_estado_comprado(self):
        pid, item_id = self._item_de_prueba(precio=5000, cantidad=10)
        proyecto = registrar_compra_item(pid, self.PROPIETARIO, item_id, 10)

        item = proyecto["items"][0]
        self.assertEqual(item["estado"], "comprado")
        self.assertEqual(item["cantidad_comprada"], 10)
        self.assertEqual(item["monto_comprado"], 50000)  # 10*5000, monto no dado

    def test_compra_parcial_pasa_a_estado_parcial(self):
        pid, item_id = self._item_de_prueba(precio=5000, cantidad=10)
        proyecto = registrar_compra_item(pid, self.PROPIETARIO, item_id, 4)

        item = proyecto["items"][0]
        self.assertEqual(item["estado"], "parcial")
        self.assertEqual(item["cantidad_comprada"], 4)
        self.assertEqual(item["monto_comprado"], 20000)

    def test_dos_registros_parciales_se_acumulan(self):
        pid, item_id = self._item_de_prueba(precio=5000, cantidad=10)
        registrar_compra_item(pid, self.PROPIETARIO, item_id, 4, monto=21000)  # precio real más alto
        proyecto = registrar_compra_item(pid, self.PROPIETARIO, item_id, 6, monto=28000)

        item = proyecto["items"][0]
        self.assertEqual(item["estado"], "comprado")  # 4+6 = 10 = cantidad total
        self.assertEqual(item["cantidad_comprada"], 10)
        self.assertEqual(item["monto_comprado"], 49000)  # 21000 + 28000, montos reales, no recalculados

    def test_monto_explicito_se_usa_en_vez_del_estimado(self):
        pid, item_id = self._item_de_prueba(precio=5000, cantidad=10)
        proyecto = registrar_compra_item(pid, self.PROPIETARIO, item_id, 10, monto=48500)

        self.assertEqual(proyecto["items"][0]["monto_comprado"], 48500)

    def test_cantidad_que_excede_lo_pendiente_se_recorta(self):
        pid, item_id = self._item_de_prueba(precio=5000, cantidad=10)
        proyecto = registrar_compra_item(pid, self.PROPIETARIO, item_id, 1000)  # error de dedo

        item = proyecto["items"][0]
        self.assertEqual(item["cantidad_comprada"], 10)  # recortado a la cantidad real del ítem
        self.assertEqual(item["estado"], "comprado")

    def test_comprobante_referencia_se_guarda(self):
        pid, item_id = self._item_de_prueba()
        proyecto = registrar_compra_item(
            pid, self.PROPIETARIO, item_id, 10, comprobante_referencia="FAC-00123"
        )
        item = proyecto["items"][0]
        self.assertEqual(item["comprobante_referencia"], "FAC-00123")
        self.assertEqual(item["comprobante_tipo"], "manual")

    def test_fecha_compra_se_guarda(self):
        pid, item_id = self._item_de_prueba()
        proyecto = registrar_compra_item(pid, self.PROPIETARIO, item_id, 10, fecha="2026-08-10 09:00:00")
        self.assertEqual(proyecto["items"][0]["fecha_compra"], "2026-08-10 09:00:00")

    def test_cantidad_cero_o_negativa_lanza_value_error(self):
        pid, item_id = self._item_de_prueba()
        with self.assertRaises(ValueError):
            registrar_compra_item(pid, self.PROPIETARIO, item_id, 0)
        with self.assertRaises(ValueError):
            registrar_compra_item(pid, self.PROPIETARIO, item_id, -5)

    def test_monto_negativo_lanza_value_error(self):
        pid, item_id = self._item_de_prueba()
        with self.assertRaises(ValueError):
            registrar_compra_item(pid, self.PROPIETARIO, item_id, 5, monto=-100)

    def test_item_ya_completo_no_permite_registrar_mas(self):
        pid, item_id = self._item_de_prueba(cantidad=10)
        registrar_compra_item(pid, self.PROPIETARIO, item_id, 10)
        with self.assertRaises(ValueError):
            registrar_compra_item(pid, self.PROPIETARIO, item_id, 1)

    def test_item_o_proyecto_inexistente_devuelve_none(self):
        pid, item_id = self._item_de_prueba()
        self.assertIsNone(registrar_compra_item(999999, self.PROPIETARIO, item_id, 1))
        self.assertIsNone(registrar_compra_item(pid, "otro-propietario", item_id, 1))
        self.assertIsNone(registrar_compra_item(pid, self.PROPIETARIO, 999999, 1))


class PruebaSincronizacionSelectorRapidoDeEstado(BasePruebaIntegracion):
    def _item_de_prueba(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg", "precio": 5000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Casa selector rápido")
        pid = proyecto["id"]
        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 10)
        return pid, proyecto["items"][0]["id"]

    def test_marcar_comprado_por_el_selector_fija_cantidad_comprada_completa(self):
        pid, item_id = self._item_de_prueba()
        proyecto = actualizar_item(pid, self.PROPIETARIO, item_id, {"estado": "comprado"})

        item = proyecto["items"][0]
        self.assertEqual(item["cantidad_comprada"], 10)
        self.assertIsNone(item["monto_comprado"])  # sin monto real -- _calcular_totales usa cantidad×precio

    def test_volver_a_pendiente_limpia_el_rastro_de_compra(self):
        pid, item_id = self._item_de_prueba()
        actualizar_item(pid, self.PROPIETARIO, item_id, {"estado": "comprado"})
        proyecto = actualizar_item(pid, self.PROPIETARIO, item_id, {"estado": "pendiente"})

        item = proyecto["items"][0]
        self.assertEqual(item["cantidad_comprada"], 0)
        self.assertIsNone(item["monto_comprado"])
        self.assertIsNone(item["fecha_compra"])

    def test_marcar_comprado_via_selector_alimenta_total_comprado(self):
        pid, item_id = self._item_de_prueba()
        proyecto = actualizar_item(pid, self.PROPIETARIO, item_id, {"estado": "comprado"})
        self.assertEqual(proyecto["total_comprado"], 50000)


class PruebaIntegracionConControlDeCostos(BasePruebaIntegracion):
    """El requisito explícito de la misión: Compras debe "alimentar
    automáticamente" Control de Costos sin que Compras llame a Control
    de Costos ni duplique su cálculo -- ambos leen _calcular_totales()."""

    def test_una_compra_parcial_se_refleja_de_inmediato_en_control_de_costos(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg", "precio": 10000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Casa integración")
        pid = proyecto["id"]
        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 10)  # 100,000
        item_id = proyecto["items"][0]["id"]
        congelar_presupuesto(pid, self.PROPIETARIO)

        registrar_compra_item(pid, self.PROPIETARIO, item_id, 3)  # 30,000 de 100,000

        control = obtener_control_costos(pid, self.PROPIETARIO)
        self.assertEqual(control["gasto_real"], 30000)
        self.assertEqual(control["saldo_disponible"], 70000)
        self.assertEqual(control["estado"], "en_curso")

    def test_completar_la_compra_parcial_mueve_control_de_costos_a_gastado_completo(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg", "precio": 10000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Casa integración 2")
        pid = proyecto["id"]
        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 10)
        item_id = proyecto["items"][0]["id"]
        congelar_presupuesto(pid, self.PROPIETARIO)

        registrar_compra_item(pid, self.PROPIETARIO, item_id, 3)
        registrar_compra_item(pid, self.PROPIETARIO, item_id, 7)

        control = obtener_control_costos(pid, self.PROPIETARIO)
        self.assertEqual(control["gasto_real"], 100000)
        self.assertEqual(control["saldo_disponible"], 0)


if __name__ == "__main__":
    unittest.main()
