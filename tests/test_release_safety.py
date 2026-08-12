"""Regresiones del gate de migraciones y readiness de producción."""

import asyncio
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

import db
import database.migraciones as migraciones
import database.agregar_calculo_compra as migracion_calculo
import database.agregar_control_costos as migracion_control_costos
import database.agregar_equivalencias as migracion_equivalencias
import database.agregar_familias_producto as migracion_familias
import database.agregar_indice_busqueda as migracion_fts
import database.agregar_proyectos as migracion_proyectos
import database.crear_base as migracion_fundacional


def _conectar(ruta):
    conexion = sqlite3.connect(ruta, timeout=10)
    conexion.execute("PRAGMA journal_mode = WAL")
    conexion.execute("PRAGMA busy_timeout = 10000")
    conexion.row_factory = sqlite3.Row
    return conexion


class PruebaSeguridadReleaseMigraciones(unittest.TestCase):
    def setUp(self):
        archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        archivo.close()
        self.ruta = archivo.name
        self.patch_conectar = mock.patch.object(
            migraciones, "conectar", lambda: _conectar(self.ruta)
        )
        self.patch_handler = mock.patch.object(
            migraciones, "_asegurar_handler", lambda: None
        )
        self.patch_conectar.start()
        self.patch_handler.start()

    def tearDown(self):
        self.patch_conectar.stop()
        self.patch_handler.stop()
        for sufijo in ("", "-wal", "-shm", ".migraciones.lock"):
            try:
                os.remove(self.ruta + sufijo)
            except FileNotFoundError:
                pass

    def _contexto(self, cuerpo):
        return (
            mock.patch.object(migraciones, "MIGRACIONES", [("prueba", cuerpo)]),
            mock.patch.object(
                migraciones,
                "REQUISITOS_ESQUEMA",
                {"prueba": {"tablas": {"tabla_prueba"}}},
            ),
        )

    def _crear_tabla(self, conexion):
        conexion.execute("CREATE TABLE tabla_prueba (id INTEGER PRIMARY KEY)")

    def _crear_tabla_manual(self):
        conexion = _conectar(self.ruta)
        self._crear_tabla(conexion)
        conexion.commit()
        conexion.close()

    def _registrada(self):
        conexion = _conectar(self.ruta)
        try:
            existe = conexion.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='migraciones_aplicadas'"
            ).fetchone()
            if not existe:
                return False
            return conexion.execute(
                "SELECT 1 FROM migraciones_aplicadas WHERE nombre='prueba'"
            ).fetchone() is not None
        finally:
            conexion.close()

    def _tabla_existe(self):
        conexion = _conectar(self.ruta)
        try:
            return conexion.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tabla_prueba'"
            ).fetchone() is not None
        finally:
            conexion.close()

    def test_interrupcion_antes_del_commit_no_marca_y_reintenta(self):
        intentos = {"cantidad": 0}

        def interrumpida(conexion):
            intentos["cantidad"] += 1
            conexion.execute("CREATE TABLE tabla_prueba (id INTEGER PRIMARY KEY)")
            raise RuntimeError("proceso terminado antes de commit")

        parches = self._contexto(interrumpida)
        with parches[0], parches[1], self.assertRaises(migraciones.ErrorEsquema):
            migraciones.aplicar_migraciones_pendientes()

        self.assertFalse(self._registrada())
        self.assertFalse(self._tabla_existe())

        parches = self._contexto(self._crear_tabla)
        with parches[0], parches[1]:
            migraciones.aplicar_migraciones_pendientes()
        self.assertTrue(self._registrada())
        self.assertTrue(self._tabla_existe())

    def test_interrupcion_antes_del_marcador_revierte_esquema_y_datos(self):
        parches = self._contexto(self._crear_tabla)
        with parches[0], parches[1], mock.patch.object(
            migraciones, "_marcar_aplicada", side_effect=RuntimeError("terminación simulada")
        ), self.assertRaises(RuntimeError):
            migraciones.aplicar_migraciones_pendientes()

        self.assertFalse(self._tabla_existe())
        self.assertFalse(self._registrada())

        parches = self._contexto(self._crear_tabla)
        with parches[0], parches[1]:
            migraciones.aplicar_migraciones_pendientes()
        self.assertTrue(self._tabla_existe())
        self.assertTrue(self._registrada())

    def test_interrupcion_despues_del_commit_conserva_esquema_y_marcador(self):
        def confirmar_y_terminar(conexion):
            conexion.commit()
            raise RuntimeError("proceso terminado después de commit")

        parches = self._contexto(self._crear_tabla)
        with parches[0], parches[1], mock.patch.object(
            migraciones, "_confirmar_atomico", side_effect=confirmar_y_terminar
        ), self.assertRaises(RuntimeError):
            migraciones.aplicar_migraciones_pendientes()

        self.assertTrue(self._tabla_existe())
        self.assertTrue(self._registrada())

        parches = self._contexto(self._crear_tabla)
        with parches[0], parches[1]:
            migraciones.aplicar_migraciones_pendientes()

    def test_reinicio_durante_migracion_espera_y_no_duplica(self):
        entro = threading.Event()
        continuar = threading.Event()
        llamadas = []
        errores = []

        def lenta(conexion):
            llamadas.append("ejecutada")
            entro.set()
            continuar.wait(timeout=5)
            self._crear_tabla(conexion)

        def ejecutar():
            try:
                migraciones.aplicar_migraciones_pendientes()
            except Exception as error:
                errores.append(error)

        parches = self._contexto(lenta)
        with parches[0], parches[1]:
            primero = threading.Thread(target=ejecutar)
            segundo = threading.Thread(target=ejecutar)
            primero.start()
            self.assertTrue(entro.wait(timeout=5))
            segundo.start()
            continuar.set()
            primero.join(timeout=10)
            segundo.join(timeout=10)

        self.assertEqual(errores, [])
        self.assertEqual(llamadas, ["ejecutada"])
        self.assertTrue(self._registrada())

    def test_registro_sin_esquema_falla_rapido(self):
        conexion = _conectar(self.ruta)
        migraciones._asegurar_tabla_seguimiento(conexion)
        migraciones._marcar_aplicada(conexion, "prueba")
        conexion.commit()
        conexion.close()

        parches = self._contexto(self._crear_tabla)
        with parches[0], parches[1], self.assertRaisesRegex(
            migraciones.ErrorEsquema, "registro=aplicada esquema=incompleto"
        ):
            migraciones.aplicar_migraciones_pendientes()

    def test_esquema_sin_registro_falla_rapido(self):
        self._crear_tabla_manual()
        parches = self._contexto(self._crear_tabla)
        with parches[0], parches[1], self.assertRaisesRegex(
            migraciones.ErrorEsquema, "BASE_LEGACY_DESCONOCIDA"
        ):
            migraciones.aplicar_migraciones_pendientes()


class PruebaBootstrapBases(unittest.TestCase):
    def setUp(self):
        archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        archivo.close()
        self.ruta = archivo.name
        self.patch_database_path = mock.patch.object(db, "BASE_DATOS", self.ruta)
        self.patch_handler = mock.patch.object(
            migraciones, "_asegurar_handler", lambda: None
        )
        self.patch_database_path.start()
        self.patch_handler.start()

    def tearDown(self):
        self.patch_database_path.stop()
        self.patch_handler.stop()
        for sufijo in ("", "-wal", "-shm", ".migraciones.lock"):
            try:
                os.remove(self.ruta + sufijo)
            except FileNotFoundError:
                pass

    def _conexion(self):
        return _conectar(self.ruta)

    def _crear_baseline_legacy(self):
        migraciones._m_esquema_fundacional.main()
        migraciones._m_familias_producto.main()
        migraciones._m_indice_busqueda.main()
        migraciones._m_proyectos.main()
        migraciones._m_cotizaciones.main()
        migraciones._m_equivalencias.main()
        migraciones._m_plano_proyecto.main()
        migraciones._m_trazabilidad_items.main()

    def test_base_completamente_vacia_crea_todo_el_esquema(self):
        migraciones.aplicar_migraciones_pendientes()

        conexion = self._conexion()
        try:
            registradas = {
                fila[0]
                for fila in conexion.execute(
                    "SELECT nombre FROM migraciones_aplicadas"
                ).fetchall()
            }
            self.assertEqual(
                registradas,
                {nombre for nombre, _ in migraciones.MIGRACIONES},
            )
            for nombre, _ in migraciones.MIGRACIONES:
                self.assertEqual(
                    migraciones._faltantes_esquema(conexion, nombre),
                    [],
                    nombre,
                )
        finally:
            conexion.close()

    def test_todas_las_migraciones_respetan_la_transaccion_del_runner(self):
        conexion = self._conexion()
        migraciones._asegurar_tabla_seguimiento(conexion)
        conexion.close()

        for nombre, funcion in migraciones.MIGRACIONES:
            with self.subTest(migracion=nombre):
                conexion = self._conexion()
                conexion.execute("BEGIN IMMEDIATE")
                resultado = funcion(conexion)
                self.assertTrue(
                    conexion.in_transaction,
                    f"{nombre} confirmó o cerró la transacción del runner",
                )
                self.assertEqual(migraciones._faltantes_esquema(conexion, nombre), [])
                self.assertEqual(
                    migraciones._faltantes_postcondiciones(
                        conexion, nombre, resultado
                    ),
                    [],
                )
                conexion.rollback()
                self.assertEqual(migraciones._estado_esquema(conexion, nombre), "ausente")
                self.assertFalse(migraciones._esta_aplicada(conexion, nombre))
                conexion.close()

                migraciones._procesar_una(nombre, funcion)

    def test_base_historica_sin_registro_adopta_solo_esquemas_completos(self):
        self._crear_baseline_legacy()
        conexion = self._conexion()
        self.assertFalse(migraciones._tabla_seguimiento_existe(conexion))
        conexion.close()

        migraciones.aplicar_migraciones_pendientes()

        conexion = self._conexion()
        try:
            registradas = {
                fila[0]
                for fila in conexion.execute(
                    "SELECT nombre FROM migraciones_aplicadas"
                ).fetchall()
            }
            self.assertEqual(
                registradas,
                {nombre for nombre, _ in migraciones.MIGRACIONES},
            )
            self.assertEqual(
                conexion.execute("SELECT COUNT(*) FROM productos").fetchone()[0], 0
            )
        finally:
            conexion.close()

    def test_registro_preexistente_adopta_el_nuevo_marcador_fundacional(self):
        self._crear_baseline_legacy()
        conexion = self._conexion()
        migraciones._asegurar_tabla_seguimiento(conexion)
        for nombre in migraciones.BASELINE_LEGACY_REQUERIDA - {
            "crear_esquema_fundacional"
        }:
            migraciones._marcar_aplicada(conexion, nombre)
        conexion.commit()
        conexion.close()

        migraciones.aplicar_migraciones_pendientes()

        conexion = self._conexion()
        try:
            self.assertTrue(
                migraciones._esta_aplicada(
                    conexion, "crear_esquema_fundacional"
                )
            )
            self.assertEqual(
                conexion.execute(
                    "SELECT COUNT(*) FROM migraciones_aplicadas"
                ).fetchone()[0],
                len(migraciones.MIGRACIONES),
            )
        finally:
            conexion.close()

    def test_ejecucion_repetida_no_duplica_registro_ni_esquema(self):
        migraciones.aplicar_migraciones_pendientes()
        migraciones.aplicar_migraciones_pendientes()

        conexion = self._conexion()
        try:
            total_registro = conexion.execute(
                "SELECT COUNT(*) FROM migraciones_aplicadas"
            ).fetchone()[0]
            columnas_productos = conexion.execute(
                "PRAGMA table_info(productos)"
            ).fetchall()
            self.assertEqual(total_registro, len(migraciones.MIGRACIONES))
            self.assertEqual(
                sum(fila[1] == "familia_id" for fila in columnas_productos), 1
            )
        finally:
            conexion.close()

    def test_base_parcial_sin_registro_falla_sin_adoptar_marcadores(self):
        migraciones._m_esquema_fundacional.main()
        conexion = self._conexion()
        conexion.execute("CREATE TABLE familias_producto (id INTEGER PRIMARY KEY)")
        conexion.commit()
        conexion.close()

        with self.assertRaisesRegex(
            migraciones.ErrorEsquema, "BASE_LEGACY_INCONSISTENTE"
        ):
            migraciones.aplicar_migraciones_pendientes()

        conexion = self._conexion()
        try:
            self.assertFalse(migraciones._tabla_seguimiento_existe(conexion))
        finally:
            conexion.close()


class PruebaAtomicidadMigracionesReales(unittest.TestCase):
    def setUp(self):
        archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        archivo.close()
        self.ruta = archivo.name
        self.patch_database_path = mock.patch.object(db, "BASE_DATOS", self.ruta)
        self.patch_database_path.start()

    def tearDown(self):
        self.patch_database_path.stop()
        for sufijo in ("", "-wal", "-shm", ".migraciones.lock"):
            try:
                os.remove(self.ruta + sufijo)
            except FileNotFoundError:
                pass

    def _conexion(self):
        return _conectar(self.ruta)

    def _crear_catalogo(self):
        migracion_fundacional.main()
        conexion = self._conexion()
        conexion.executemany(
            """
            INSERT INTO productos (
                proveedor, id_proveedor, nombre, marca, categoria, subcategoria
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("Proveedor A", "a1", "Pintura Latex Blanco 1 Galon ABC1234", "Lanco", "Pinturas", "Interior"),
                ("Proveedor A", "a2", "Pintura Latex Blanco 1 Litro ABC1234", "Lanco", "Pinturas", "Interior"),
                ("Proveedor B", "b1", "Taladro Percutor Bosch GSB13RE 650W", "Bosch", "Herramientas", "Taladros"),
                ("Proveedor C", "c1", "Taladro Percutor Bosch GSB13RE 650W", "Bosch", "Herramientas", "Taladros"),
            ],
        )
        conexion.commit()
        conexion.close()

    def _probar_rollback_sin_commit_interno(self, nombre, funcion, preparar, tabla):
        preparar()
        conexion = self._conexion()
        migraciones._asegurar_tabla_seguimiento(conexion)
        conexion.execute("BEGIN IMMEDIATE")
        resultado = funcion(conexion)
        self.assertTrue(conexion.in_transaction)
        self.assertEqual(
            migraciones._faltantes_esquema(conexion, nombre),
            [],
        )
        self.assertEqual(
            migraciones._faltantes_postcondiciones(conexion, nombre, resultado),
            [],
        )
        conexion.rollback()
        existe = conexion.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tabla,)
        ).fetchone()
        self.assertIsNone(existe)
        self.assertFalse(migraciones._esta_aplicada(conexion, nombre))
        conexion.close()

    def test_familias_reales_no_confirman_fuera_del_runner(self):
        self._probar_rollback_sin_commit_interno(
            "agregar_familias_producto",
            migracion_familias.main,
            self._crear_catalogo,
            "familias_producto",
        )

    def test_fts_real_no_confirma_fuera_del_runner(self):
        self._probar_rollback_sin_commit_interno(
            "agregar_indice_busqueda",
            migracion_fts.main,
            self._crear_catalogo,
            "productos_fts",
        )

    def test_equivalencias_reales_no_confirman_fuera_del_runner(self):
        self._probar_rollback_sin_commit_interno(
            "agregar_equivalencias",
            migracion_equivalencias.main,
            self._crear_catalogo,
            "grupos_equivalencia",
        )

    def test_conversiones_reales_no_confirman_fuera_del_runner(self):
        migracion_fundacional.main()
        migracion_proyectos.main()
        migracion_control_costos.main()
        self._probar_rollback_sin_commit_interno(
            "agregar_calculo_compra",
            migracion_calculo.main,
            lambda: None,
            "conversiones_unidad",
        )


class PruebaReadinessStartup(unittest.TestCase):
    def test_migracion_fallida_impide_llegar_al_yield(self):
        import api.main as api_main

        alcanzo_ready = {"valor": False}

        async def iniciar():
            async with api_main._lifespan(api_main.app):
                alcanzo_ready["valor"] = True

        with mock.patch.object(
            api_main,
            "_aplicar_migraciones_pendientes",
            side_effect=migraciones.ErrorEsquema("mismatch"),
        ), mock.patch.object(api_main.threading, "Thread") as hilo:
            with self.assertRaises(migraciones.ErrorEsquema):
                asyncio.run(iniciar())

        self.assertFalse(alcanzo_ready["valor"])
        hilo.assert_not_called()

    def test_ready_ocurre_despues_de_migrar_y_verificar(self):
        import api.main as api_main

        orden = []
        hilo = mock.Mock()
        hilo.start.side_effect = lambda: orden.append("respaldos")

        async def iniciar():
            async with api_main._lifespan(api_main.app):
                orden.append("ready")

        with mock.patch.object(
            api_main,
            "_aplicar_migraciones_pendientes",
            side_effect=lambda: orden.append("esquema"),
        ), mock.patch.object(api_main.threading, "Thread", return_value=hilo):
            asyncio.run(iniciar())

        self.assertEqual(orden, ["esquema", "respaldos", "ready"])


if __name__ == "__main__":
    unittest.main()
