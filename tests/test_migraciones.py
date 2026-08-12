"""Pruebas para database/migraciones.py -- el runner que ejecuta las
migraciones pendientes en el arranque (ver el hallazgo real que lo
originó: producción falló con "no such table: usuarios" porque nada
ejecutaba database/agregar_autenticacion.py contra la base real).

Se prueban los mecanismos del runner (transacción atómica, rollback,
salteo de lo ya aplicado y concurrencia bajo --workers 4) contra un
registro de migraciones FALSO. Las regresiones con migraciones reales y
sus efectos de datos viven en tests/test_release_safety.py."""

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

import database.migraciones as migraciones
import database.agregar_calculo_compra as migracion_calculo


def _conexion_temporal(ruta):
    conexion = sqlite3.connect(ruta, timeout=10)
    conexion.execute("PRAGMA journal_mode = WAL")
    conexion.execute("PRAGMA busy_timeout = 10000")
    conexion.row_factory = sqlite3.Row
    return conexion


class PruebaRegistroFinalizacion(unittest.TestCase):
    def setUp(self):
        archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        archivo.close()
        self.ruta_db = archivo.name
        self.conexion = _conexion_temporal(self.ruta_db)
        migraciones._asegurar_tabla_seguimiento(self.conexion)

    def tearDown(self):
        self.conexion.close()
        for sufijo in ("", "-wal", "-shm"):
            try:
                os.remove(self.ruta_db + sufijo)
            except FileNotFoundError:
                pass

    def test_no_aplicada_hasta_marcar_despues_del_commit(self):
        self.assertFalse(migraciones._esta_aplicada(self.conexion, "una_migracion"))
        migraciones._marcar_aplicada(self.conexion, "una_migracion")
        self.assertTrue(migraciones._esta_aplicada(self.conexion, "una_migracion"))
        self.conexion.rollback()
        self.assertFalse(migraciones._esta_aplicada(self.conexion, "una_migracion"))


class PruebaOrden(unittest.TestCase):
    def test_esquema_fundacional_es_primero_y_autenticacion_segunda(self):
        primeros_nombres = [nombre for nombre, _ in migraciones.MIGRACIONES[:2]]
        self.assertEqual(
            primeros_nombres,
            ["crear_esquema_fundacional", "agregar_autenticacion"],
            "productos debe existir antes de cualquier migración histórica que la altere",
        )


class PruebaMigracionCalculoCompra(unittest.TestCase):
    def setUp(self):
        archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        archivo.close()
        self.ruta_db = archivo.name
        conexion = _conexion_temporal(self.ruta_db)
        conexion.executescript(
            """
            CREATE TABLE proyectos (id INTEGER PRIMARY KEY);
            CREATE TABLE items_proyecto (id INTEGER PRIMARY KEY, estado_calculo TEXT);
            CREATE TABLE presupuesto_congelado (id INTEGER PRIMARY KEY);
            INSERT INTO proyectos(id) VALUES (1);
            INSERT INTO items_proyecto(id, estado_calculo) VALUES (1, NULL);
            INSERT INTO presupuesto_congelado(id) VALUES (1);
            """
        )
        conexion.commit()
        conexion.close()
        self._patch = mock.patch.object(
            migracion_calculo, "conectar", lambda: _conexion_temporal(self.ruta_db)
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        for sufijo in ("", "-wal", "-shm"):
            try:
                os.remove(self.ruta_db + sufijo)
            except FileNotFoundError:
                pass

    def test_es_aditiva_y_preserva_filas_legacy(self):
        migracion_calculo.main()
        conexion = _conexion_temporal(self.ruta_db)
        proyecto = conexion.execute(
            "SELECT version_calculo_cotizacion FROM proyectos WHERE id = 1"
        ).fetchone()
        item = conexion.execute(
            "SELECT estado_calculo, cantidad_requerida FROM items_proyecto WHERE id = 1"
        ).fetchone()
        self.assertEqual(proyecto["version_calculo_cotizacion"], 1)
        self.assertIsNone(item["estado_calculo"])
        self.assertIsNone(item["cantidad_requerida"])
        self.assertEqual(
            conexion.execute("SELECT COUNT(*) FROM presupuesto_congelado").fetchone()[0], 1
        )
        conexion.close()

    def test_reejecucion_no_duplica_columnas_ni_conversiones(self):
        migracion_calculo.main()
        migracion_calculo.main()
        conexion = _conexion_temporal(self.ruta_db)
        columnas = conexion.execute("PRAGMA table_info(items_proyecto)").fetchall()
        self.assertEqual(sum(f["name"] == "unidades_compra" for f in columnas), 1)
        galon = conexion.execute(
            "SELECT factor_a_canonica FROM conversiones_unidad WHERE unidad_origen = 'galon'"
        ).fetchone()
        libra = conexion.execute(
            "SELECT factor_a_canonica FROM conversiones_unidad WHERE unidad_origen = 'lb'"
        ).fetchone()
        self.assertEqual(galon["factor_a_canonica"], "3.785411784")
        self.assertEqual(libra["factor_a_canonica"], "0.45359237")
        conexion.close()

    def test_verificador_real_acepta_esquema_y_conversiones_p0(self):
        migracion_calculo.main()
        conexion = _conexion_temporal(self.ruta_db)
        self.assertEqual(
            migraciones._faltantes_esquema(conexion, "agregar_calculo_compra"), []
        )
        conexion.close()

    def test_verificador_real_detecta_conversion_autoritativa_alterada(self):
        migracion_calculo.main()
        conexion = _conexion_temporal(self.ruta_db)
        conexion.execute(
            "UPDATE conversiones_unidad SET factor_a_canonica='3.78' WHERE unidad_origen='galon'"
        )
        conexion.commit()
        self.assertIn(
            "conversion:galon",
            migraciones._faltantes_esquema(conexion, "agregar_calculo_compra"),
        )
        conexion.close()


class PruebaMigracionesCompletadas(unittest.TestCase):
    def setUp(self):
        archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        archivo.close()
        self.ruta_db = archivo.name
        self._patch_conectar = mock.patch.object(
            migraciones, "conectar", lambda: _conexion_temporal(self.ruta_db)
        )
        self._patch_conectar.start()

    def tearDown(self):
        self._patch_conectar.stop()
        for sufijo in ("", "-wal", "-shm"):
            try:
                os.remove(self.ruta_db + sufijo)
            except FileNotFoundError:
                pass

    def test_lista_vacia_si_la_tabla_de_seguimiento_no_existe_todavia(self):
        self.assertEqual(migraciones.migraciones_completadas(), [])

    def test_devuelve_los_nombres_ya_aplicados(self):
        conexion = _conexion_temporal(self.ruta_db)
        migraciones._asegurar_tabla_seguimiento(conexion)
        migraciones._marcar_aplicada(conexion, "agregar_autenticacion")
        migraciones._marcar_aplicada(conexion, "agregar_proyectos")
        conexion.commit()
        conexion.close()
        self.assertEqual(
            set(migraciones.migraciones_completadas()), {"agregar_autenticacion", "agregar_proyectos"}
        )


class PruebaOrquestacion(unittest.TestCase):
    """Reemplaza MIGRACIONES por un registro falso y conectar() por una
    fábrica de conexiones a un archivo temporal -- prueba el runner
    entero (aplicar_migraciones_pendientes), no solo _reclamar."""

    def setUp(self):
        archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        archivo.close()
        self.ruta_db = archivo.name
        self._patch_conectar = mock.patch.object(
            migraciones, "conectar", lambda: _conexion_temporal(self.ruta_db)
        )
        self._patch_conectar.start()
        self._patch_handler = mock.patch.object(migraciones, "_asegurar_handler", lambda: None)
        self._patch_handler.start()

    def tearDown(self):
        self._patch_conectar.stop()
        self._patch_handler.stop()
        for sufijo in ("", "-wal", "-shm", ".migraciones.lock"):
            try:
                os.remove(self.ruta_db + sufijo)
            except FileNotFoundError:
                pass

    def test_migracion_pendiente_se_ejecuta_y_queda_registrada(self):
        llamadas = []

        def cuerpo(conexion):
            llamadas.append(1)
            conexion.execute("CREATE TABLE prueba_esquema (id INTEGER)")

        with mock.patch.object(migraciones, "MIGRACIONES", [("prueba", cuerpo)]), mock.patch.object(
            migraciones, "REQUISITOS_ESQUEMA", {"prueba": {"tablas": {"prueba_esquema"}}}
        ):
            migraciones.aplicar_migraciones_pendientes()
        self.assertEqual(llamadas, [1])
        conexion = _conexion_temporal(self.ruta_db)
        fila = conexion.execute(
            "SELECT nombre FROM migraciones_aplicadas WHERE nombre = 'prueba'"
        ).fetchone()
        conexion.close()
        self.assertIsNotNone(fila)

    def test_migracion_ya_aplicada_no_se_vuelve_a_ejecutar(self):
        llamadas = []

        def cuerpo(conexion):
            llamadas.append(1)
            conexion.execute("CREATE TABLE prueba_esquema (id INTEGER)")

        migracion_falsa = [("prueba", cuerpo)]
        with mock.patch.object(migraciones, "MIGRACIONES", migracion_falsa), mock.patch.object(
            migraciones, "REQUISITOS_ESQUEMA", {"prueba": {"tablas": {"prueba_esquema"}}}
        ):
            migraciones.aplicar_migraciones_pendientes()  # 1er arranque: corre
            migraciones.aplicar_migraciones_pendientes()  # 2do arranque: se salta
        self.assertEqual(llamadas, [1])

    def test_migracion_fallida_no_se_marca_y_permite_reintentar(self):
        intentos = {"n": 0}

        def cuerpo_que_falla_la_primera_vez(conexion):
            intentos["n"] += 1
            if intentos["n"] == 1:
                raise RuntimeError("falla simulada")
            conexion.execute("CREATE TABLE prueba_esquema (id INTEGER)")

        with mock.patch.object(
            migraciones, "MIGRACIONES", [("prueba", cuerpo_que_falla_la_primera_vez)]
        ), mock.patch.object(
            migraciones, "REQUISITOS_ESQUEMA", {"prueba": {"tablas": {"prueba_esquema"}}}
        ):
            with self.assertRaises(migraciones.ErrorEsquema):
                migraciones.aplicar_migraciones_pendientes()
            self.assertEqual(intentos["n"], 1)
            migraciones.aplicar_migraciones_pendientes()  # reintenta, esta vez pasa
            self.assertEqual(intentos["n"], 2)

        conexion = _conexion_temporal(self.ruta_db)
        fila = conexion.execute(
            "SELECT nombre FROM migraciones_aplicadas WHERE nombre = 'prueba'"
        ).fetchone()
        conexion.close()
        self.assertIsNotNone(fila, "tras el reintento exitoso, sí debe quedar registrada")

    def test_una_migracion_que_falla_bloquea_readiness_y_las_siguientes(self):
        llamadas = []
        migracion_falsa = [
            ("rota", lambda conexion: (_ for _ in ()).throw(RuntimeError("siempre falla"))),
            ("sana", lambda conexion: llamadas.append("sana")),
        ]
        requisitos = {
            "rota": {"tablas": {"rota_esquema"}},
            "sana": {"tablas": {"sana_esquema"}},
        }
        with mock.patch.object(migraciones, "MIGRACIONES", migracion_falsa), mock.patch.object(
            migraciones, "REQUISITOS_ESQUEMA", requisitos
        ), self.assertRaises(migraciones.ErrorEsquema):
            migraciones.aplicar_migraciones_pendientes()
        self.assertEqual(llamadas, [])

    def test_falla_no_emite_resumen_exitoso(self):
        migracion_falsa = [
            ("rota", lambda conexion: (_ for _ in ()).throw(RuntimeError("siempre falla")))
        ]
        with mock.patch.object(migraciones, "MIGRACIONES", migracion_falsa), mock.patch.object(
            migraciones, "REQUISITOS_ESQUEMA", {"rota": {"tablas": {"rota_esquema"}}}
        ), mock.patch.object(migraciones, "logger") as logger_falso, self.assertRaises(
            migraciones.ErrorEsquema
        ):
            migraciones.aplicar_migraciones_pendientes()
        self.assertFalse(any("RESUMEN" in llamada.args[0] for llamada in logger_falso.info.call_args_list))

    def test_resumen_final_reporta_todas_al_dia_cuando_no_hay_fallas(self):
        def sana(conexion):
            conexion.execute("CREATE TABLE sana_esquema (id INTEGER)")

        migracion_falsa = [("sana", sana)]
        with mock.patch.object(migraciones, "MIGRACIONES", migracion_falsa), mock.patch.object(
            migraciones, "REQUISITOS_ESQUEMA", {"sana": {"tablas": {"sana_esquema"}}}
        ), mock.patch.object(
            migraciones, "logger"
        ) as logger_falso:
            migraciones.aplicar_migraciones_pendientes()

        mensajes_info = [llamada.args[0] for llamada in logger_falso.info.call_args_list]
        self.assertTrue(
            any(m.startswith("RESUMEN 1/1 migraciones aplicadas") and "esquema verificado" in m for m in mensajes_info),
            f"no se encontró la línea RESUMEN esperada entre: {mensajes_info}",
        )


class PruebaConcurrencia(unittest.TestCase):
    """El lock de ejecución está separado del registro de finalización."""

    def setUp(self):
        archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        archivo.close()
        self.ruta_db = archivo.name
        conexion = _conexion_temporal(self.ruta_db)
        conexion.close()
        self._patch_conectar = mock.patch.object(
            migraciones, "conectar", lambda: _conexion_temporal(self.ruta_db)
        )
        self._patch_conectar.start()

    def tearDown(self):
        self._patch_conectar.stop()
        for sufijo in ("", "-wal", "-shm", ".migraciones.lock"):
            try:
                os.remove(self.ruta_db + sufijo)
            except FileNotFoundError:
                pass

    def test_solo_una_instancia_entra_al_runner_a_la_vez(self):
        activos = 0
        maximo_activos = 0
        lock = threading.Lock()
        barrera = threading.Barrier(4)
        errores = []

        def worker():
            nonlocal activos, maximo_activos
            try:
                barrera.wait()
                with migraciones._bloqueo_exclusivo_runner():
                    with lock:
                        activos += 1
                        maximo_activos = max(maximo_activos, activos)
                    time.sleep(0.02)
                    with lock:
                        activos -= 1
            except Exception as error:
                errores.append(error)

        hilos = [threading.Thread(target=worker) for _ in range(4)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=10)

        self.assertEqual(errores, [])
        self.assertEqual(maximo_activos, 1)


if __name__ == "__main__":
    unittest.main()
