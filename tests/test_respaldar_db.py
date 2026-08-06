"""Prueba para database/respaldar_db.py -- ver la migración a
/data/proyecta.db: antes, los respaldos vivían en un directorio
hardcodeado (database/respaldos/), sin importar dónde estuviera la base
real. Si BASE_DATOS apunta al disco persistente de Render, guardar los
respaldos en el checkout efímero del repo los habría vuelto inútiles --
un redeploy los borra a todos. Ahora el directorio de respaldos se deriva
de BASE_DATOS (mismo directorio, subcarpeta respaldos/), así que sigue a
la base real esté donde esté."""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import database.respaldar_db as respaldar_db


class PruebaRespaldar(unittest.TestCase):
    def setUp(self):
        self.directorio = tempfile.mkdtemp()
        self.ruta_db = os.path.join(self.directorio, "proyecta.db")
        conexion = sqlite3.connect(self.ruta_db)
        conexion.execute("CREATE TABLE productos (id INTEGER PRIMARY KEY, nombre TEXT)")
        conexion.execute("INSERT INTO productos (nombre) VALUES ('cemento')")
        conexion.commit()
        conexion.close()
        self._patch = mock.patch.object(respaldar_db, "BASE_DATOS", self.ruta_db)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_el_respaldo_queda_junto_a_la_base_real_no_en_una_ruta_fija(self):
        codigo = respaldar_db.respaldar()
        self.assertEqual(codigo, 0)

        directorio_respaldos = os.path.join(self.directorio, "respaldos")
        self.assertTrue(os.path.isdir(directorio_respaldos))
        archivos = os.listdir(directorio_respaldos)
        self.assertEqual(len(archivos), 1)
        self.assertTrue(archivos[0].startswith("proyecta_") and archivos[0].endswith(".db"))

        conexion = sqlite3.connect(os.path.join(directorio_respaldos, archivos[0]))
        fila = conexion.execute("SELECT nombre FROM productos").fetchone()
        conexion.close()
        self.assertEqual(fila[0], "cemento")

    def test_no_hardcodea_database_respaldos(self):
        ruta_real = "database/respaldos"
        antes = set(os.listdir(ruta_real)) if os.path.isdir(ruta_real) else set()

        respaldar_db.respaldar()

        despues = set(os.listdir(ruta_real)) if os.path.isdir(ruta_real) else set()
        self.assertEqual(
            antes, despues,
            "el respaldo cayó en database/respaldos aunque BASE_DATOS apuntaba a otro directorio",
        )

    def test_purga_respaldos_viejos_en_el_directorio_correcto(self):
        for _ in range(3):
            respaldar_db.respaldar(mantener=2)
        directorio_respaldos = os.path.join(self.directorio, "respaldos")
        archivos = os.listdir(directorio_respaldos)
        self.assertLessEqual(len(archivos), 2)

    def test_sin_base_no_falla_no_crea_directorio(self):
        self._patch.stop()
        self._patch = mock.patch.object(respaldar_db, "BASE_DATOS", os.path.join(self.directorio, "no_existe.db"))
        self._patch.start()
        codigo = respaldar_db.respaldar()
        self.assertEqual(codigo, 1)


if __name__ == "__main__":
    unittest.main()
