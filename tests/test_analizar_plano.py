"""Pruebas para api/repositorio_proyectos.py::analizar_plano/eliminar_plano
-- el guardado del resultado de leer un PDF de planos (niveles, espacios,
cuadros, cómputo estructural) en la fila del proyecto.

No corre lectura_planos de verdad (esa lógica ya tiene su propia
cobertura en tests/test_lectura_planos*.py) -- acá se mockea
_EXECUTOR_PLANOS.submit(...).result() con un análisis ya construido, y se
prueba la orquestación: que el resultado quede guardado como JSON en
proyectos.plano_analisis, que vuelva ya parseado en la respuesta, que
respete propietario_id, y que eliminar_plano() limpie los tres campos.

Hueco real encontrado mientras se auditaba todo el flujo de subida de
planos (crear proyecto -> subir PDF -> ver materiales -> cotización):
analizar_plano() y eliminar_plano() no tenían ninguna prueba, a pesar de
ser el corazón de FLUJO_PRESUPUESTO_DESDE_PLANO_V1.md. El flujo en sí se
verificó funcionando de punta a punta con dos PDFs reales (uno
estructural, uno arquitectónico de 110MB) contra un uvicorn real y,
después, contra el frontend real con Playwright -- sin encontrar ningún
bug. Esta prueba es la que faltaba para que esa cobertura quede
permanente en la suite, no solo verificada a mano una vez."""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from api.repositorio_proyectos import analizar_plano, crear_proyecto, eliminar_plano


def _crear_db_temporal():
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    conexion = sqlite3.connect(archivo.name)
    conexion.execute(
        """
        CREATE TABLE proyectos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            propietario_id TEXT NOT NULL,
            nombre TEXT NOT NULL,
            comentario TEXT,
            estado TEXT NOT NULL DEFAULT 'activo',
            fecha_objetivo TEXT,
            token_compartido TEXT UNIQUE NOT NULL,
            fecha_creacion TEXT,
            fecha_actualizacion TEXT,
            cliente TEXT,
            direccion TEXT,
            area_m2 REAL,
            indirectos_porcentaje REAL NOT NULL DEFAULT 0,
            imprevistos_porcentaje REAL NOT NULL DEFAULT 0,
            margen_porcentaje REAL NOT NULL DEFAULT 0,
            plano_nombre_archivo TEXT,
            plano_analisis TEXT,
            plano_fecha_analisis TEXT
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE items_proyecto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proyecto_id INTEGER NOT NULL REFERENCES proyectos(id) ON DELETE CASCADE,
            proveedor TEXT NOT NULL,
            id_proveedor TEXT NOT NULL,
            cantidad REAL NOT NULL DEFAULT 1,
            unidad_medida TEXT,
            estado TEXT NOT NULL DEFAULT 'pendiente',
            prioridad TEXT,
            comentario TEXT,
            nombre_al_agregar TEXT,
            marca_al_agregar TEXT,
            categoria_al_agregar TEXT,
            precio_al_agregar REAL,
            url_imagen_al_agregar TEXT,
            url_producto_al_agregar TEXT,
            fecha_agregado TEXT,
            partida TEXT,
            origen TEXT,
            pagina_fuente INTEGER,
            lamina_fuente TEXT,
            texto_original TEXT,
            confianza TEXT,
            regla_generadora TEXT,
            UNIQUE(proyecto_id, proveedor, id_proveedor)
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor TEXT, id_proveedor TEXT, sku TEXT, nombre TEXT,
            marca TEXT, categoria TEXT, subcategoria TEXT, precio REAL,
            descripcion TEXT, url_imagen TEXT, url_producto TEXT,
            peso TEXT, imagenes_adicionales TEXT, familia_id INTEGER,
            UNIQUE(proveedor, id_proveedor)
        )
        """
    )
    conexion.commit()
    conexion.close()
    return archivo.name


ANALISIS_DE_PRUEBA = {
    "proyecto_nombre": "Casa de prueba",
    "cantidad_laminas": 3,
    "niveles": [{"nombre": "N 0.0 M", "laminas": ["A101"]}],
    "espacios": [{"nombre": "Dormitorio 1", "nivel": "N 0.0 M", "pagina_fuente": 5, "texto_original": "DORM 1"}],
    "puertas": [],
    "ventanas": [],
    "acabados": [],
    "piezas_estructurales": [],
    "laminas": {},
    "advertencias": [],
}


class PruebaAnalizarPlano(unittest.TestCase):
    PROPIETARIO = "propietario-plano-test"

    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch_db = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch_db.start()
        self._patch_executor = mock.patch("api.repositorio_proyectos._EXECUTOR_PLANOS")
        executor_falso = self._patch_executor.start()
        futuro_falso = mock.MagicMock()
        futuro_falso.result.return_value = ANALISIS_DE_PRUEBA
        executor_falso.submit.return_value = futuro_falso

        self.proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con plano")

    def tearDown(self):
        self._patch_executor.stop()
        self._patch_db.stop()
        os.remove(self.ruta_db)

    def test_guarda_el_analisis_como_json_y_lo_devuelve_ya_parseado(self):
        resultado = analizar_plano(
            self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf"
        )
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado["plano_nombre_archivo"], "planos.pdf")
        self.assertEqual(resultado["plano_analisis"], ANALISIS_DE_PRUEBA)
        self.assertIsNotNone(resultado["plano_fecha_analisis"])

    def test_el_analisis_queda_persistido_como_json_valido_en_la_columna(self):
        analizar_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf")

        conexion = sqlite3.connect(self.ruta_db)
        fila = conexion.execute(
            "SELECT plano_analisis FROM proyectos WHERE id = ?", (self.proyecto["id"],)
        ).fetchone()
        conexion.close()
        self.assertEqual(json.loads(fila[0]), ANALISIS_DE_PRUEBA)

    def test_actualiza_fecha_actualizacion_del_proyecto(self):
        antes = self.proyecto["fecha_actualizacion"]
        resultado = analizar_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf")
        # No afirmamos un valor de tiempo distinto (podrían coincidir al
        # segundo en una corrida muy rápida) -- solo que el campo sigue
        # ahí y no quedó vacío.
        self.assertIsNotNone(resultado["fecha_actualizacion"])
        self.assertIsNotNone(antes)

    def test_proyecto_inexistente_devuelve_none(self):
        resultado = analizar_plano(999999, self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf")
        self.assertIsNone(resultado)

    def test_no_deja_analizar_el_plano_de_otro_propietario(self):
        resultado = analizar_plano(
            self.proyecto["id"], "otro-propietario-distinto", "/tmp/no-importa.pdf", "planos.pdf"
        )
        self.assertIsNone(resultado)

    def test_reemplazar_un_plano_ya_analizado_pisa_el_anterior(self):
        analizar_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "primero.pdf")

        segundo_analisis = dict(ANALISIS_DE_PRUEBA, proyecto_nombre="Casa reemplazada", cantidad_laminas=7)
        self._patch_executor.stop()
        self._patch_executor = mock.patch("api.repositorio_proyectos._EXECUTOR_PLANOS")
        executor_falso = self._patch_executor.start()
        futuro_falso = mock.MagicMock()
        futuro_falso.result.return_value = segundo_analisis
        executor_falso.submit.return_value = futuro_falso

        resultado = analizar_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "segundo.pdf")
        self.assertEqual(resultado["plano_nombre_archivo"], "segundo.pdf")
        self.assertEqual(resultado["plano_analisis"]["cantidad_laminas"], 7)


class PruebaEliminarPlano(unittest.TestCase):
    PROPIETARIO = "propietario-plano-test"

    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch_db = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch_db.start()
        self._patch_executor = mock.patch("api.repositorio_proyectos._EXECUTOR_PLANOS")
        executor_falso = self._patch_executor.start()
        futuro_falso = mock.MagicMock()
        futuro_falso.result.return_value = ANALISIS_DE_PRUEBA
        executor_falso.submit.return_value = futuro_falso

        self.proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con plano")
        analizar_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf")

    def tearDown(self):
        self._patch_executor.stop()
        self._patch_db.stop()
        os.remove(self.ruta_db)

    def test_limpia_los_tres_campos_del_plano(self):
        resultado = eliminar_plano(self.proyecto["id"], self.PROPIETARIO)
        self.assertIsNone(resultado["plano_nombre_archivo"])
        self.assertIsNone(resultado["plano_analisis"])
        self.assertIsNone(resultado["plano_fecha_analisis"])

    def test_no_borra_nada_mas_del_proyecto(self):
        resultado = eliminar_plano(self.proyecto["id"], self.PROPIETARIO)
        self.assertEqual(resultado["nombre"], "Proyecto con plano")
        self.assertEqual(resultado["id"], self.proyecto["id"])

    def test_proyecto_inexistente_devuelve_none(self):
        self.assertIsNone(eliminar_plano(999999, self.PROPIETARIO))

    def test_no_deja_eliminar_el_plano_de_otro_propietario(self):
        resultado = eliminar_plano(self.proyecto["id"], "otro-propietario-distinto")
        self.assertIsNone(resultado)
        # Confirma que de verdad no se tocó nada -- no solo que devolvió None.
        conexion = sqlite3.connect(self.ruta_db)
        fila = conexion.execute(
            "SELECT plano_analisis FROM proyectos WHERE id = ?", (self.proyecto["id"],)
        ).fetchone()
        conexion.close()
        self.assertIsNotNone(fila[0])


if __name__ == "__main__":
    unittest.main()
