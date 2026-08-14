import json
import sqlite3
import threading
import time
import unittest
from unittest import mock

import api.repositorio_proyectos as repositorio
from api.repositorio_proyectos import (
    agregar_item,
    crear_proyecto,
    generar_orden_compra,
    registrar_compra_item,
    reemplazar_item,
)
from tests.test_repositorio_proyectos import BasePruebaIntegracion


TIMEOUT = 5


class _CoordinadorBeginImmediate:
    """Pausa la primera operación justo después de tomar el write lock."""

    def __init__(self, conectar_real):
        self._conectar_real = conectar_real
        self.primera_con_lock = threading.Event()
        self.liberar_primera = threading.Event()
        self._lock = threading.Lock()
        self._begin_pausado = False

    def conectar(self):
        conexion_real = self._conectar_real()
        return _ConexionPausada(conexion_real, self)

    def debe_pausar_begin(self):
        with self._lock:
            if self._begin_pausado:
                return False
            self._begin_pausado = True
            return True


class _ConexionPausada:
    def __init__(self, conexion, coordinador):
        self._conexion = conexion
        self._coordinador = coordinador

    def execute(self, sql, parametros=()):
        resultado = self._conexion.execute(sql, parametros)
        if sql.strip().upper() == "BEGIN IMMEDIATE" and self._coordinador.debe_pausar_begin():
            self._coordinador.primera_con_lock.set()
            if not self._coordinador.liberar_primera.wait(TIMEOUT):
                raise TimeoutError("La prueba no liberó la primera transacción")
        return resultado

    def __getattr__(self, nombre):
        return getattr(self._conexion, nombre)


def _ejecutar_en_hilo(funcion, resultados, clave):
    try:
        resultados[clave] = ("ok", funcion())
    except BaseException as exc:
        resultados[clave] = ("error", exc)


class BaseComprasP0(BasePruebaIntegracion):
    def _producto(self, identificador, nombre=None, precio=100):
        self._insertar_productos([
            {
                "proveedor": "EPA",
                "id_proveedor": str(identificador),
                "nombre": nombre or f"Producto {identificador}",
                "categoria": "Construcción",
                "precio": precio,
            }
        ])

    def _proyecto_item(self, cantidad=10, producto="1"):
        self._producto(producto)
        proyecto = crear_proyecto(self.PROPIETARIO, "Obra P0")
        proyecto = agregar_item(
            proyecto["id"], self.PROPIETARIO, "EPA", str(producto), cantidad
        )
        return proyecto["id"], proyecto["items"][0]["id"]

    def _fila_item(self, item_id):
        conexion = sqlite3.connect(self.ruta_db)
        conexion.row_factory = sqlite3.Row
        fila = conexion.execute("SELECT * FROM items_proyecto WHERE id = ?", (item_id,)).fetchone()
        conexion.close()
        return dict(fila) if fila else None

    def _snapshot_items(self, proyecto_id):
        conexion = sqlite3.connect(self.ruta_db)
        conexion.row_factory = sqlite3.Row
        filas = conexion.execute(
            "SELECT * FROM items_proyecto WHERE proyecto_id = ? ORDER BY id", (proyecto_id,)
        ).fetchall()
        conexion.close()
        return json.dumps([dict(fila) for fila in filas], sort_keys=True, ensure_ascii=False)


class PruebaP0NumeracionOrdenes(BaseComprasP0):
    def test_dos_ordenes_concurrentes_obtienen_numeros_unicos(self):
        pid, _ = self._proyecto_item()
        coordinador = _CoordinadorBeginImmediate(repositorio.conectar)
        resultados = {}

        with mock.patch.object(repositorio, "conectar", coordinador.conectar):
            primero = threading.Thread(
                target=_ejecutar_en_hilo,
                args=(lambda: generar_orden_compra(pid, self.PROPIETARIO, "EPA"), resultados, "a"),
            )
            primero.start()
            self.assertTrue(coordinador.primera_con_lock.wait(TIMEOUT), "La primera orden no tomó el lock")

            segundo = threading.Thread(
                target=_ejecutar_en_hilo,
                args=(lambda: generar_orden_compra(pid, self.PROPIETARIO, "EPA"), resultados, "b"),
            )
            segundo.start()
            time.sleep(0.05)
            self.assertTrue(segundo.is_alive(), "La segunda orden no esperó el write lock")
            coordinador.liberar_primera.set()
            primero.join(TIMEOUT)
            segundo.join(TIMEOUT)

        self.assertFalse(primero.is_alive(), "La primera orden quedó bloqueada")
        self.assertFalse(segundo.is_alive(), "La segunda orden quedó bloqueada")
        self.assertEqual({resultado[0] for resultado in resultados.values()}, {"ok"})
        conexion = sqlite3.connect(self.ruta_db)
        numeros = [
            fila[0] for fila in conexion.execute(
                "SELECT numero FROM ordenes_compra WHERE proyecto_id = ? ORDER BY numero", (pid,)
            )
        ]
        conexion.close()
        self.assertEqual(numeros, [f"OC-{pid}-1", f"OC-{pid}-2"])

    def test_usa_mayor_sufijo_valido_e_ignora_numeros_no_estandar(self):
        pid, _ = self._proyecto_item()
        conexion = sqlite3.connect(self.ruta_db)
        numeros_previos = (
            f"OC-{pid}-2",
            f"OC-{pid}-7",
            f"OC-{pid}-reservado",
            "LEGACY-PRIVADO",
        )
        for numero in numeros_previos:
            conexion.execute(
                """
                INSERT INTO ordenes_compra (
                    proyecto_id, proveedor, numero, fecha_creacion, monto_total, snapshot_json
                ) VALUES (?, 'EPA', ?, '2026-01-01', 0, '[]')
                """,
                (pid, numero),
            )
        conexion.commit()
        conexion.close()

        with mock.patch.object(repositorio._logger, "info") as registrar_log:
            compras = generar_orden_compra(pid, self.PROPIETARIO, "EPA")

        numeros = {orden["numero"] for orden in compras["ordenes_generadas"]}
        self.assertIn(f"OC-{pid}-8", numeros)
        registrar_log.assert_called_once_with(
            "ORDEN_COMPRA_NUMERO_NO_ESTANDAR_IGNORADO proyecto_id=%s cantidad=%s",
            pid,
            2,
        )
        texto_log = " ".join(str(valor) for valor in registrar_log.call_args.args)
        self.assertNotIn("reservado", texto_log)
        self.assertNotIn("LEGACY-PRIVADO", texto_log)

    def test_interrupcion_revierte_reserva_y_orden(self):
        pid, _ = self._proyecto_item()
        conectar_real = repositorio.conectar

        class ConexionInterrumpida(_ConexionPausada):
            def execute(self, sql, parametros=()):
                resultado = self._conexion.execute(sql, parametros)
                if sql.lstrip().startswith("INSERT INTO ordenes_compra"):
                    raise KeyboardInterrupt("interrupción inyectada")
                return resultado

        class Coordinador:
            def conectar(self):
                return ConexionInterrumpida(conectar_real(), self)

        with mock.patch.object(repositorio, "conectar", Coordinador().conectar):
            with self.assertRaises(KeyboardInterrupt):
                generar_orden_compra(pid, self.PROPIETARIO, "EPA")

        conexion = sqlite3.connect(self.ruta_db)
        self.assertEqual(conexion.execute("SELECT COUNT(*) FROM ordenes_compra").fetchone()[0], 0)
        conexion.close()


class PruebaP0ComprasEstrictas(BaseComprasP0):
    def _compras_concurrentes(self, cantidad_a, cantidad_b, monto_a, monto_b):
        pid, item_id = self._proyecto_item(cantidad=10)
        coordinador = _CoordinadorBeginImmediate(repositorio.conectar)
        resultados = {}
        with mock.patch.object(repositorio, "conectar", coordinador.conectar):
            a = threading.Thread(
                target=_ejecutar_en_hilo,
                args=(lambda: registrar_compra_item(pid, self.PROPIETARIO, item_id, cantidad_a, monto=monto_a), resultados, "a"),
            )
            a.start()
            self.assertTrue(coordinador.primera_con_lock.wait(TIMEOUT), "La primera compra no tomó el lock")
            b = threading.Thread(
                target=_ejecutar_en_hilo,
                args=(lambda: registrar_compra_item(pid, self.PROPIETARIO, item_id, cantidad_b, monto=monto_b), resultados, "b"),
            )
            b.start()
            time.sleep(0.05)
            self.assertTrue(b.is_alive(), "La segunda compra no esperó el write lock")
            coordinador.liberar_primera.set()
            a.join(TIMEOUT)
            b.join(TIMEOUT)
        self.assertFalse(a.is_alive(), "La primera compra quedó bloqueada")
        self.assertFalse(b.is_alive(), "La segunda compra quedó bloqueada")
        return item_id, resultados

    def test_dos_compras_de_7_una_confirma_y_otra_rechaza_sin_clamp(self):
        item_id, resultados = self._compras_concurrentes(7, 7, 700, 770)
        estados = sorted(resultado[0] for resultado in resultados.values())
        self.assertEqual(estados, ["error", "ok"])
        error = next(resultado[1] for resultado in resultados.values() if resultado[0] == "error")
        self.assertIsInstance(error, ValueError)
        fila = self._fila_item(item_id)
        self.assertEqual(fila["cantidad_comprada"], 7)
        self.assertEqual(fila["monto_comprado"], 700)

    def test_dos_compras_de_4_ambas_confirman_y_final_es_8(self):
        item_id, resultados = self._compras_concurrentes(4, 4, 400, 440)
        self.assertEqual({resultado[0] for resultado in resultados.values()}, {"ok"})
        fila = self._fila_item(item_id)
        self.assertEqual(fila["cantidad_comprada"], 8)
        self.assertEqual(fila["monto_comprado"], 840)

    def test_cantidad_exactamente_pendiente_confirma_completa(self):
        pid, item_id = self._proyecto_item(cantidad=10)
        registrar_compra_item(pid, self.PROPIETARIO, item_id, 4, monto=400)
        registrar_compra_item(pid, self.PROPIETARIO, item_id, 6, monto=600)
        fila = self._fila_item(item_id)
        self.assertEqual(fila["cantidad_comprada"], 10)
        self.assertEqual(fila["monto_comprado"], 1000)
        self.assertEqual(fila["estado"], "comprado")

    def test_equivalencia_decimal_0_1_normaliza_sin_desajustar_monto(self):
        pid, item_id = self._proyecto_item(cantidad=0.3, producto="decimal-01")
        registrar_compra_item(pid, self.PROPIETARIO, item_id, 0.1)
        registrar_compra_item(pid, self.PROPIETARIO, item_id, 0.2)
        fila = self._fila_item(item_id)
        self.assertEqual(fila["cantidad_comprada"], 0.3)
        self.assertEqual(fila["monto_comprado"], 30)
        self.assertEqual(fila["estado"], "comprado")

    def test_equivalencia_decimal_1_25_conserva_cantidad_y_monto(self):
        pid, item_id = self._proyecto_item(cantidad=2.5, producto="decimal-125")
        registrar_compra_item(pid, self.PROPIETARIO, item_id, 1.25, monto=125)
        registrar_compra_item(pid, self.PROPIETARIO, item_id, 1.25, monto=125)
        fila = self._fila_item(item_id)
        self.assertEqual(fila["cantidad_comprada"], 2.5)
        self.assertEqual(fila["monto_comprado"], 250)
        self.assertEqual(fila["estado"], "comprado")

    def test_cantidades_pequenas_equivalentes_cierran_sin_exceso(self):
        pid, item_id = self._proyecto_item(cantidad=0.0003, producto="decimal-pequeno")
        registrar_compra_item(pid, self.PROPIETARIO, item_id, 0.0001, monto=1)
        registrar_compra_item(pid, self.PROPIETARIO, item_id, 0.0002, monto=2)
        fila = self._fila_item(item_id)
        self.assertEqual(fila["cantidad_comprada"], 0.0003)
        self.assertEqual(fila["monto_comprado"], 3)
        self.assertEqual(fila["estado"], "comprado")

    def test_exceso_real_fuera_de_tolerancia_revierte_cantidad_y_monto(self):
        pid, item_id = self._proyecto_item(cantidad=0.3, producto="decimal-exceso")
        registrar_compra_item(pid, self.PROPIETARIO, item_id, 0.1, monto=10)
        antes = self._fila_item(item_id)
        with self.assertRaisesRegex(ValueError, "excede"):
            registrar_compra_item(pid, self.PROPIETARIO, item_id, 0.200001, monto=999)
        self.assertEqual(self._fila_item(item_id), antes)

    def test_tolerancia_no_crece_con_cantidades_grandes(self):
        pid, item_id = self._proyecto_item(cantidad=1_000_000, producto="decimal-maximo")
        antes = self._fila_item(item_id)
        with self.assertRaisesRegex(ValueError, "excede"):
            registrar_compra_item(
                pid,
                self.PROPIETARIO,
                item_id,
                1_000_000.000001,
                monto=999,
            )
        self.assertEqual(self._fila_item(item_id), antes)

    def test_cantidad_mayor_a_pendiente_hace_rollback_total(self):
        pid, item_id = self._proyecto_item(cantidad=10)
        registrar_compra_item(
            pid, self.PROPIETARIO, item_id, 4, monto=400,
            fecha="2026-01-01", comprobante_referencia="FAC-BASE",
        )
        antes = self._fila_item(item_id)
        with self.assertRaisesRegex(ValueError, "excede"):
            registrar_compra_item(
                pid, self.PROPIETARIO, item_id, 7, monto=999,
                fecha="2026-02-02", comprobante_referencia="FAC-NUEVA",
            )
        self.assertEqual(self._fila_item(item_id), antes)

    def test_interrupcion_revierte_todos_los_campos(self):
        pid, item_id = self._proyecto_item(cantidad=10)
        antes = self._fila_item(item_id)
        conectar_real = repositorio.conectar

        class ConexionInterrumpida:
            def __init__(self):
                self._conexion = conectar_real()

            def execute(self, sql, parametros=()):
                resultado = self._conexion.execute(sql, parametros)
                if sql.lstrip().startswith("UPDATE proyectos"):
                    raise KeyboardInterrupt("interrupción inyectada")
                return resultado

            def __getattr__(self, nombre):
                return getattr(self._conexion, nombre)

        with mock.patch.object(repositorio, "conectar", lambda: ConexionInterrumpida()):
            with self.assertRaises(KeyboardInterrupt):
                registrar_compra_item(
                    pid, self.PROPIETARIO, item_id, 3, monto=333,
                    fecha="2026-03-03", comprobante_referencia="FAC-X",
                )
        self.assertEqual(self._fila_item(item_id), antes)


class PruebaP0ReemplazoAtomico(BaseComprasP0):
    def _dos_productos(self):
        self._producto("1", "Origen")
        self._producto("2", "Destino")
        proyecto = crear_proyecto(self.PROPIETARIO, "Obra reemplazo")
        proyecto = agregar_item(proyecto["id"], self.PROPIETARIO, "EPA", "1", 10)
        return proyecto["id"], proyecto["items"][0]["id"]

    def test_origen_comprado_rechaza_y_snapshot_permanece_identico(self):
        pid, origen_id = self._dos_productos()
        registrar_compra_item(pid, self.PROPIETARIO, origen_id, 1, monto=100)
        antes = self._snapshot_items(pid)
        with self.assertRaisesRegex(ValueError, "compras registradas"):
            reemplazar_item(pid, self.PROPIETARIO, origen_id, "EPA", "2")
        self.assertEqual(self._snapshot_items(pid), antes)

    def test_destino_existente_sin_compras_rechaza_y_snapshot_permanece_identico(self):
        pid, origen_id = self._dos_productos()
        agregar_item(pid, self.PROPIETARIO, "EPA", "2", 1)
        antes = self._snapshot_items(pid)
        with self.assertRaisesRegex(ValueError, "ya existe"):
            reemplazar_item(pid, self.PROPIETARIO, origen_id, "EPA", "2")
        self.assertEqual(self._snapshot_items(pid), antes)

    def test_destino_comprado_rechaza_y_snapshot_permanece_identico(self):
        pid, origen_id = self._dos_productos()
        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "2", 2)
        destino_id = next(item["id"] for item in proyecto["items"] if item["id_proveedor"] == "2")
        registrar_compra_item(pid, self.PROPIETARIO, destino_id, 1, monto=100)
        antes = self._snapshot_items(pid)
        with self.assertRaisesRegex(ValueError, "ya existe"):
            reemplazar_item(pid, self.PROPIETARIO, origen_id, "EPA", "2")
        self.assertEqual(self._snapshot_items(pid), antes)

    def _carrera_compra_reemplazo(self, comprar_destino):
        pid, origen_id = self._dos_productos()
        if comprar_destino:
            proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "2", 2)
            comprado_id = next(item["id"] for item in proyecto["items"] if item["id_proveedor"] == "2")
        else:
            comprado_id = origen_id
        coordinador = _CoordinadorBeginImmediate(repositorio.conectar)
        resultados = {}
        with mock.patch.object(repositorio, "conectar", coordinador.conectar):
            compra = threading.Thread(
                target=_ejecutar_en_hilo,
                args=(lambda: registrar_compra_item(pid, self.PROPIETARIO, comprado_id, 1, monto=100), resultados, "compra"),
            )
            compra.start()
            self.assertTrue(coordinador.primera_con_lock.wait(TIMEOUT), "La compra no tomó el lock")
            reemplazo = threading.Thread(
                target=_ejecutar_en_hilo,
                args=(lambda: reemplazar_item(pid, self.PROPIETARIO, origen_id, "EPA", "2"), resultados, "reemplazo"),
            )
            reemplazo.start()
            time.sleep(0.05)
            self.assertTrue(reemplazo.is_alive(), "El reemplazo no esperó el write lock")
            coordinador.liberar_primera.set()
            compra.join(TIMEOUT)
            reemplazo.join(TIMEOUT)
        self.assertFalse(compra.is_alive(), "La compra quedó bloqueada")
        self.assertFalse(reemplazo.is_alive(), "El reemplazo quedó bloqueado")
        self.assertEqual(resultados["compra"][0], "ok")
        self.assertEqual(resultados["reemplazo"][0], "error")
        return pid, comprado_id, resultados["reemplazo"][1]

    def test_carrera_compra_origen_vs_reemplazo_observa_compra_y_rechaza(self):
        pid, comprado_id, error = self._carrera_compra_reemplazo(comprar_destino=False)
        self.assertRegex(str(error), "compras registradas")
        self.assertEqual(self._fila_item(comprado_id)["cantidad_comprada"], 1)
        self.assertEqual(len(json.loads(self._snapshot_items(pid))), 1)

    def test_carrera_compra_destino_vs_reemplazo_observa_destino_y_rechaza(self):
        pid, comprado_id, error = self._carrera_compra_reemplazo(comprar_destino=True)
        self.assertRegex(str(error), "ya existe")
        self.assertEqual(self._fila_item(comprado_id)["cantidad_comprada"], 1)
        self.assertEqual(len(json.loads(self._snapshot_items(pid))), 2)

    def test_interrupcion_revierte_delete_insert_evento_y_proyecto(self):
        pid, origen_id = self._dos_productos()
        antes = self._snapshot_items(pid)
        conectar_real = repositorio.conectar

        class ConexionInterrumpida:
            def __init__(self):
                self._conexion = conectar_real()

            def execute(self, sql, parametros=()):
                resultado = self._conexion.execute(sql, parametros)
                if sql.lstrip().startswith("UPDATE proyectos"):
                    raise KeyboardInterrupt("interrupción inyectada")
                return resultado

            def __getattr__(self, nombre):
                return getattr(self._conexion, nombre)

        with mock.patch.object(repositorio, "conectar", lambda: ConexionInterrumpida()):
            with self.assertRaises(KeyboardInterrupt):
                reemplazar_item(pid, self.PROPIETARIO, origen_id, "EPA", "2")
        self.assertEqual(self._snapshot_items(pid), antes)


if __name__ == "__main__":
    unittest.main()
