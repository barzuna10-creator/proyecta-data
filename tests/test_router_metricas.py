"""Pruebas para api/routers/metricas.py -- los endpoints de dashboards
internos son envoltorios delgados sobre eventos.py (ya probado a fondo
en tests/test_eventos.py); acá solo se confirma que el router llama a
la función correcta y devuelve la forma de respuesta esperada."""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import eventos
from api.routers.metricas import (
    categorias_peor_desempeno,
    materiales_mas_dificiles,
    resumen_seleccion_automatica,
)


def _crear_db_temporal():
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    conexion = sqlite3.connect(archivo.name)
    conexion.execute(
        """
        CREATE TABLE eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            usuario_id TEXT,
            proyecto_id INTEGER,
            item_id INTEGER,
            proveedor TEXT,
            id_proveedor TEXT,
            proveedor_anterior TEXT,
            id_proveedor_anterior TEXT,
            categoria TEXT,
            origen TEXT,
            confianza_match TEXT,
            texto_material TEXT,
            tiempo_hasta_decision_segundos REAL,
            datos_extra TEXT,
            fecha_creacion TEXT NOT NULL
        )
        """
    )
    conexion.commit()
    conexion.close()
    return archivo.name


class PruebaRouterMetricas(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.remove(self.ruta_db)

    def _registrar(self, tipo, **campos):
        conexion = sqlite3.connect(self.ruta_db)
        eventos.registrar_evento(conexion, tipo, **campos)
        conexion.commit()
        conexion.close()

    def test_resumen_seleccion_automatica_devuelve_la_forma_esperada(self):
        self._registrar(eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="alta")
        respuesta = resumen_seleccion_automatica(dias=None, _usuario_autenticado="u1")
        self.assertIn("por_confianza", respuesta)
        self.assertIn("total", respuesta)
        self.assertEqual(respuesta["total"]["sugeridas"], 1)

    def test_materiales_dificiles_devuelve_lista_bajo_la_clave_materiales(self):
        respuesta = materiales_mas_dificiles(limite=20, dias=None, _usuario_autenticado="u1")
        self.assertEqual(respuesta, {"materiales": []})

    def test_categorias_peor_desempeno_devuelve_lista_bajo_la_clave_categorias(self):
        respuesta = categorias_peor_desempeno(limite=20, dias=None, _usuario_autenticado="u1")
        self.assertEqual(respuesta, {"categorias": []})

    def test_limite_se_respeta_de_extremo_a_extremo(self):
        for i in range(5):
            self._registrar(
                eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="media",
                texto_material=f"material {i}",
            )
            self._registrar(
                eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="media",
                texto_material=f"material {i}",
            )
        respuesta = materiales_mas_dificiles(limite=2, dias=None, _usuario_autenticado="u1")
        self.assertEqual(len(respuesta["materiales"]), 2)


if __name__ == "__main__":
    unittest.main()
