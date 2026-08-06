"""Prueba para api/routers/feedback.py (ver BETA_1.0_CHECKLIST.md,
hallazgo 7.1: "ningún canal para que un ingeniero reporte un problema").

No hay TestClient (requeriría httpx, no instalado) -- crear_feedback() es
una función Python normal sin `async def` ni contexto de request real más
allá del propio body, así que se llama directo, mismo patrón que
tests/test_routers_proyectos.py."""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from api.routers.feedback import FeedbackRequest, crear_feedback


def _crear_db_temporal():
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()
    conexion = sqlite3.connect(archivo.name)
    conexion.execute("""
        CREATE TABLE feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id TEXT,
            mensaje TEXT NOT NULL,
            pagina TEXT,
            fecha_creacion TEXT NOT NULL
        )
    """)
    conexion.commit()
    conexion.close()
    return archivo.name


class PruebaCrearFeedback(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.remove(self.ruta_db)

    def test_guarda_el_mensaje_con_el_usuario_y_la_pagina(self):
        crear_feedback(
            FeedbackRequest(mensaje="La búsqueda de puertas no encuentra nada", pagina="/proyectos/12"),
            propietario_id="usuario-123",
        )
        import db
        conexion = db.conectar()
        fila = conexion.execute("SELECT usuario_id, mensaje, pagina FROM feedback").fetchone()
        conexion.close()
        self.assertEqual(fila["usuario_id"], "usuario-123")
        self.assertEqual(fila["mensaje"], "La búsqueda de puertas no encuentra nada")
        self.assertEqual(fila["pagina"], "/proyectos/12")

    def test_recorta_espacios_del_mensaje(self):
        crear_feedback(FeedbackRequest(mensaje="  con espacios  "), propietario_id="usuario-123")
        import db
        conexion = db.conectar()
        fila = conexion.execute("SELECT mensaje FROM feedback").fetchone()
        conexion.close()
        self.assertEqual(fila["mensaje"], "con espacios")


if __name__ == "__main__":
    unittest.main()
