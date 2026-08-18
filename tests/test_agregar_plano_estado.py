"""Pruebas para database/agregar_plano_estado.py -- la migración de
Mission #002 (Plan Processing Stability) que agrega plano_estado,
plano_error_mensaje y plano_procesamiento_id a proyectos, con backfill
para proyectos que ya tenían un plano_analisis guardado de antes."""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import database.agregar_plano_estado as migracion


def _crear_db_temporal_pre_migracion():
    """Esquema mínimo de proyectos ANTES de esta migración -- ya incluye
    las tres columnas de agregar_plano_proyecto.py (plano_nombre_archivo,
    plano_analisis, plano_fecha_analisis), que es la migración de la que
    esta depende."""
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    conexion = sqlite3.connect(archivo.name)
    conexion.execute(
        """
        CREATE TABLE proyectos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            propietario_id TEXT NOT NULL,
            nombre TEXT NOT NULL,
            plano_nombre_archivo TEXT,
            plano_analisis TEXT,
            plano_fecha_analisis TEXT
        )
        """
    )
    conexion.commit()
    conexion.close()
    return archivo.name


class PruebaAgregarPlanoEstado(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal_pre_migracion()
        import db
        self._patch_db = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch_db.start()

    def tearDown(self):
        self._patch_db.stop()
        os.remove(self.ruta_db)

    def _columnas(self):
        conexion = sqlite3.connect(self.ruta_db)
        columnas = {fila[1] for fila in conexion.execute("PRAGMA table_info(proyectos)").fetchall()}
        conexion.close()
        return columnas

    def test_agrega_las_tres_columnas(self):
        migracion.main()
        columnas = self._columnas()
        self.assertIn("plano_estado", columnas)
        self.assertIn("plano_error_mensaje", columnas)
        self.assertIn("plano_procesamiento_id", columnas)

    def test_correr_dos_veces_no_falla_ni_duplica(self):
        migracion.main()
        migracion.main()  # no debe lanzar sqlite3.OperationalError (columna duplicada)
        self.assertEqual(len(self._columnas()), len(set(self._columnas())))

    def test_backfill_marca_listo_los_proyectos_con_analisis_ya_guardado(self):
        conexion = sqlite3.connect(self.ruta_db)
        conexion.execute(
            "INSERT INTO proyectos (propietario_id, nombre, plano_analisis) VALUES (?, ?, ?)",
            ("prop-1", "Con plano ya analizado", '{"cantidad_laminas": 3}'),
        )
        conexion.execute(
            "INSERT INTO proyectos (propietario_id, nombre, plano_analisis) VALUES (?, ?, ?)",
            ("prop-1", "Sin plano", None),
        )
        conexion.commit()
        conexion.close()

        migracion.main()

        conexion = sqlite3.connect(self.ruta_db)
        conexion.row_factory = sqlite3.Row
        filas = {
            fila["nombre"]: fila["plano_estado"]
            for fila in conexion.execute("SELECT nombre, plano_estado FROM proyectos")
        }
        conexion.close()

        self.assertEqual(filas["Con plano ya analizado"], "listo")
        self.assertIsNone(filas["Sin plano"])

    def test_backfill_es_idempotente(self):
        conexion = sqlite3.connect(self.ruta_db)
        conexion.execute(
            "INSERT INTO proyectos (propietario_id, nombre, plano_analisis) VALUES (?, ?, ?)",
            ("prop-1", "Con plano", '{"cantidad_laminas": 1}'),
        )
        conexion.commit()
        conexion.close()

        migracion.main()
        # Simula que el estado ya avanzó a 'error' después del backfill
        # (ej. una recuperación de arranque real) -- correr la migración
        # de nuevo NO debe pisarlo de vuelta a 'listo', porque el guard es
        # "plano_estado IS NULL", y para este momento ya no lo es.
        conexion = sqlite3.connect(self.ruta_db)
        conexion.execute("UPDATE proyectos SET plano_estado = 'error'")
        conexion.commit()
        conexion.close()

        migracion.main()

        conexion = sqlite3.connect(self.ruta_db)
        estado = conexion.execute("SELECT plano_estado FROM proyectos").fetchone()[0]
        conexion.close()
        self.assertEqual(estado, "error")


if __name__ == "__main__":
    unittest.main()
