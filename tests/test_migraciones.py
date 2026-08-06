"""Pruebas para database/migraciones.py -- el runner que ejecuta las
migraciones pendientes en el arranque (ver el hallazgo real que lo
originó: producción falló con "no such table: usuarios" porque nada
ejecutaba database/agregar_autenticacion.py contra la base real).

Se prueban los mecanismos del runner (reclamo, liberación al fallar,
salteo de lo ya aplicado, concurrencia bajo --workers 4) contra un
registro de migraciones FALSO -- las funciones reales de
database/agregar_*.py ya están cubiertas por sus propias pruebas y no se
les tocó ni una línea; lo nuevo a probar es la orquestación."""

import os
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

import database.migraciones as migraciones


def _conexion_temporal(ruta):
    conexion = sqlite3.connect(ruta, timeout=10)
    conexion.execute("PRAGMA journal_mode = WAL")
    conexion.execute("PRAGMA busy_timeout = 10000")
    conexion.row_factory = sqlite3.Row
    return conexion


class PruebaReclamo(unittest.TestCase):
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

    def test_reclamar_devuelve_true_la_primera_vez(self):
        self.assertTrue(migraciones._reclamar(self.conexion, "una_migracion"))

    def test_reclamar_devuelve_false_si_ya_esta_reclamada(self):
        migraciones._reclamar(self.conexion, "una_migracion")
        self.assertFalse(migraciones._reclamar(self.conexion, "una_migracion"))

    def test_liberar_permite_reclamar_de_nuevo(self):
        migraciones._reclamar(self.conexion, "una_migracion")
        migraciones._liberar(self.conexion, "una_migracion")
        self.assertTrue(migraciones._reclamar(self.conexion, "una_migracion"))


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
        for sufijo in ("", "-wal", "-shm"):
            try:
                os.remove(self.ruta_db + sufijo)
            except FileNotFoundError:
                pass

    def test_migracion_pendiente_se_ejecuta_y_queda_registrada(self):
        llamadas = []
        with mock.patch.object(migraciones, "MIGRACIONES", [("prueba", lambda: llamadas.append(1))]):
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
        migracion_falsa = [("prueba", lambda: llamadas.append(1))]
        with mock.patch.object(migraciones, "MIGRACIONES", migracion_falsa):
            migraciones.aplicar_migraciones_pendientes()  # 1er arranque: corre
            migraciones.aplicar_migraciones_pendientes()  # 2do arranque: se salta
        self.assertEqual(llamadas, [1])

    def test_migracion_fallida_libera_el_reclamo_para_reintentar(self):
        intentos = {"n": 0}

        def cuerpo_que_falla_la_primera_vez():
            intentos["n"] += 1
            if intentos["n"] == 1:
                raise RuntimeError("falla simulada")

        with mock.patch.object(
            migraciones, "MIGRACIONES", [("prueba", cuerpo_que_falla_la_primera_vez)]
        ):
            migraciones.aplicar_migraciones_pendientes()  # falla, libera el reclamo
            self.assertEqual(intentos["n"], 1)
            migraciones.aplicar_migraciones_pendientes()  # reintenta, esta vez pasa
            self.assertEqual(intentos["n"], 2)

        conexion = _conexion_temporal(self.ruta_db)
        fila = conexion.execute(
            "SELECT nombre FROM migraciones_aplicadas WHERE nombre = 'prueba'"
        ).fetchone()
        conexion.close()
        self.assertIsNotNone(fila, "tras el reintento exitoso, sí debe quedar registrada")

    def test_una_migracion_que_sigue_fallando_no_bloquea_las_siguientes(self):
        llamadas = []
        migracion_falsa = [
            ("rota", lambda: (_ for _ in ()).throw(RuntimeError("siempre falla"))),
            ("sana", lambda: llamadas.append("sana")),
        ]
        with mock.patch.object(migraciones, "MIGRACIONES", migracion_falsa):
            migraciones.aplicar_migraciones_pendientes()
        self.assertEqual(llamadas, ["sana"])


class PruebaConcurrencia(unittest.TestCase):
    """Simula --workers 4: 4 hilos, cada uno con su PROPIA conexión (como
    tendría cada proceso real), reclamando la misma migración casi al
    mismo tiempo contra el mismo archivo. Solo uno debe ganar el reclamo
    -- ese es el mecanismo real que garantiza que el cuerpo de la
    migración corra una sola vez entre los 4 workers.

    Prueba _reclamar() directamente (no aplicar_migraciones_pendientes())
    a propósito: mock.patch.object sobre atributos de módulo compartidos
    (MIGRACIONES, conectar) no es seguro entre hilos concurrentes -- el
    __exit__ de un hilo puede restaurar el valor real mientras otro hilo
    todavía está a mitad de su propio `with`, y de hecho eso fue lo que
    pasó en un primer intento de esta prueba: terminó llamando a las
    migraciones reales contra database/proyecta.db real (inofensivo acá
    porque son idempotentes, pero un efecto secundario no buscado). Cada
    hilo abajo usa su propia conexión real y llama _reclamar() sin tocar
    ningún estado compartido de módulo -- ninguna carrera posible."""

    def setUp(self):
        archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        archivo.close()
        self.ruta_db = archivo.name
        conexion = _conexion_temporal(self.ruta_db)
        migraciones._asegurar_tabla_seguimiento(conexion)
        conexion.close()

    def tearDown(self):
        for sufijo in ("", "-wal", "-shm"):
            try:
                os.remove(self.ruta_db + sufijo)
            except FileNotFoundError:
                pass

    def test_solo_una_instancia_gana_el_reclamo_bajo_4_workers(self):
        resultados = []
        lock = threading.Lock()
        barrera = threading.Barrier(4)

        def worker():
            conexion = _conexion_temporal(self.ruta_db)
            barrera.wait()  # los 4 hilos reclaman lo más juntos posible
            gano = migraciones._reclamar(conexion, "compartida")
            with lock:
                resultados.append(gano)
            conexion.close()

        hilos = [threading.Thread(target=worker) for _ in range(4)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=10)

        self.assertEqual(
            resultados.count(True), 1, "más de un worker ganó el reclamo de la misma migración"
        )
        self.assertEqual(resultados.count(False), 3)

        conexion = _conexion_temporal(self.ruta_db)
        cantidad = conexion.execute(
            "SELECT COUNT(*) AS c FROM migraciones_aplicadas WHERE nombre = 'compartida'"
        ).fetchone()["c"]
        conexion.close()
        self.assertEqual(cantidad, 1)


if __name__ == "__main__":
    unittest.main()
