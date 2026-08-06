"""Prueba para api/routers/auth.py::_manejar_si_falta_tabla -- causa raíz
real de producción: /auth/registro devolvía un 500 genérico cuando
`usuarios` todavía no existía (el runner de migraciones no había
terminado o había fallado en ese proceso). Ahora debe volverse un 503
diagnosticable, con la tabla faltante y cuáles migraciones sí llegaron a
aplicarse -- y nunca ocultar un error real que no sea justamente ese."""

import sqlite3
import unittest
from unittest import mock

from fastapi import HTTPException

from api.routers.auth import _manejar_si_falta_tabla


class PruebaManejarSiFaltaTabla(unittest.TestCase):
    def test_no_such_table_se_vuelve_503_diagnosticable(self):
        with mock.patch(
            "api.routers.auth.migraciones_completadas",
            return_value=["agregar_proyectos", "agregar_familias_producto"],
        ):
            with self.assertRaises(HTTPException) as contexto:
                _manejar_si_falta_tabla(
                    sqlite3.OperationalError("no such table: usuarios"), "/auth/registro"
                )
        self.assertEqual(contexto.exception.status_code, 503)

    def test_loguea_la_tabla_faltante_y_las_migraciones_completadas(self):
        with mock.patch(
            "api.routers.auth.migraciones_completadas",
            return_value=["agregar_proyectos"],
        ), mock.patch("api.routers.auth.logger") as logger_falso:
            with self.assertRaises(HTTPException):
                _manejar_si_falta_tabla(
                    sqlite3.OperationalError("no such table: usuarios"), "/auth/registro"
                )

        mensaje = logger_falso.error.call_args[0][0]
        self.assertIn("ESQUEMA_NO_LISTO", mensaje)
        self.assertIn("endpoint=/auth/registro", mensaje)
        self.assertIn("tabla_faltante=usuarios", mensaje)
        self.assertIn("agregar_proyectos", mensaje)

    def test_otro_operational_error_no_se_oculta(self):
        """Un OperationalError que NO es "no such table" (ej. la base
        está corrupta, o locked) no debe convertirse en un 503 genérico
        de esquema -- eso ocultaría un problema distinto. Debe propagarse
        tal cual."""
        error_distinto = sqlite3.OperationalError("database is locked")
        with self.assertRaises(sqlite3.OperationalError):
            _manejar_si_falta_tabla(error_distinto, "/auth/registro")


if __name__ == "__main__":
    unittest.main()
