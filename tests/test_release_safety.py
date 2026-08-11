"""Regresiones del gate de migraciones y readiness de producción."""

import asyncio
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

import database.migraciones as migraciones


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

    def _crear_tabla_y_commit(self):
        conexion = _conectar(self.ruta)
        conexion.execute("CREATE TABLE tabla_prueba (id INTEGER PRIMARY KEY)")
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

        def interrumpida():
            intentos["cantidad"] += 1
            conexion = _conectar(self.ruta)
            try:
                conexion.execute("BEGIN")
                conexion.execute("CREATE TABLE tabla_prueba (id INTEGER PRIMARY KEY)")
                raise RuntimeError("proceso terminado antes de commit")
            finally:
                conexion.close()

        parches = self._contexto(interrumpida)
        with parches[0], parches[1], self.assertRaises(migraciones.ErrorEsquema):
            migraciones.aplicar_migraciones_pendientes()

        self.assertFalse(self._registrada())
        self.assertFalse(self._tabla_existe())

        parches = self._contexto(self._crear_tabla_y_commit)
        with parches[0], parches[1]:
            migraciones.aplicar_migraciones_pendientes()
        self.assertTrue(self._registrada())
        self.assertTrue(self._tabla_existe())

    def test_interrupcion_despues_del_commit_deja_mismatch_y_reinicio_falla(self):
        parches = self._contexto(self._crear_tabla_y_commit)
        with parches[0], parches[1], mock.patch.object(
            migraciones, "_marcar_aplicada", side_effect=RuntimeError("terminación simulada")
        ), self.assertRaises(RuntimeError):
            migraciones.aplicar_migraciones_pendientes()

        self.assertTrue(self._tabla_existe())
        self.assertFalse(self._registrada())

        parches = self._contexto(self._crear_tabla_y_commit)
        with parches[0], parches[1], self.assertRaisesRegex(
            migraciones.ErrorEsquema, "REGISTRO_ESQUEMA_INCONSISTENTE"
        ):
            migraciones.aplicar_migraciones_pendientes()

    def test_reinicio_durante_migracion_espera_y_no_duplica(self):
        entro = threading.Event()
        continuar = threading.Event()
        llamadas = []
        errores = []

        def lenta():
            llamadas.append("ejecutada")
            entro.set()
            continuar.wait(timeout=5)
            self._crear_tabla_y_commit()

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
        conexion.close()

        parches = self._contexto(self._crear_tabla_y_commit)
        with parches[0], parches[1], self.assertRaisesRegex(
            migraciones.ErrorEsquema, "registro=aplicada esquema=incompleto"
        ):
            migraciones.aplicar_migraciones_pendientes()

    def test_esquema_sin_registro_falla_rapido(self):
        self._crear_tabla_y_commit()
        parches = self._contexto(self._crear_tabla_y_commit)
        with parches[0], parches[1], self.assertRaisesRegex(
            migraciones.ErrorEsquema, "registro=ausente esquema=completo"
        ):
            migraciones.aplicar_migraciones_pendientes()


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
