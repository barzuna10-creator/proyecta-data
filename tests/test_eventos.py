"""Pruebas para eventos.py -- registrar_evento() y las consultas de
dashboard (resumen_seleccion_automatica, materiales_mas_dificiles,
categorias_peor_desempeno). Base SQLite temporal con solo la tabla
`eventos` (ver database/agregar_eventos.py) -- estas pruebas no
necesitan productos/proyectos reales, solo filas de eventos ya
registradas."""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import eventos


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


class BasePruebaEventos(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.remove(self.ruta_db)

    def _conexion(self):
        return sqlite3.connect(self.ruta_db)

    def _registrar(self, tipo, **campos):
        conexion = self._conexion()
        eventos.registrar_evento(conexion, tipo, **campos)
        conexion.commit()
        conexion.close()

    def _envejecer_ultimo_evento(self, dias):
        """Retrocede fecha_creacion del último evento insertado -- para
        probar el filtro `dias` sin depender de esperar tiempo real."""
        from datetime import datetime, timedelta

        fecha = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
        conexion = self._conexion()
        conexion.execute(
            "UPDATE eventos SET fecha_creacion = ? WHERE id = (SELECT MAX(id) FROM eventos)",
            (fecha,),
        )
        conexion.commit()
        conexion.close()

    def _filas(self):
        conexion = self._conexion()
        conexion.row_factory = sqlite3.Row
        filas = [dict(f) for f in conexion.execute("SELECT * FROM eventos ORDER BY id").fetchall()]
        conexion.close()
        return filas


class PruebaRegistrarEvento(BasePruebaEventos):
    def test_inserta_una_fila_con_todos_los_campos(self):
        self._registrar(
            eventos.TIPO_ITEM_AGREGADO,
            usuario_id="u1", proyecto_id=5, item_id=9,
            proveedor="EPA", id_proveedor="1",
            categoria="Puertas", origen="plano", confianza_match="alta",
            texto_material="puerta laurel", tiempo_hasta_decision_segundos=12.5,
            datos_extra={"nota": "prueba"},
        )
        fila = self._filas()[0]
        self.assertEqual(fila["tipo"], "item_agregado")
        self.assertEqual(fila["usuario_id"], "u1")
        self.assertEqual(fila["proyecto_id"], 5)
        self.assertEqual(fila["item_id"], 9)
        self.assertEqual(fila["proveedor"], "EPA")
        self.assertEqual(fila["categoria"], "Puertas")
        self.assertEqual(fila["confianza_match"], "alta")
        self.assertEqual(fila["texto_material"], "puerta laurel")
        self.assertEqual(fila["tiempo_hasta_decision_segundos"], 12.5)
        self.assertIn("nota", fila["datos_extra"])
        self.assertIsNotNone(fila["fecha_creacion"])

    def test_tipo_invalido_lanza_value_error(self):
        conexion = self._conexion()
        with self.assertRaises(ValueError):
            eventos.registrar_evento(conexion, "tipo_inventado")
        conexion.close()

    def test_campos_opcionales_quedan_en_none(self):
        self._registrar(eventos.TIPO_ITEM_AGREGADO)
        fila = self._filas()[0]
        self.assertIsNone(fila["usuario_id"])
        self.assertIsNone(fila["proveedor"])
        self.assertIsNone(fila["datos_extra"])


class PruebaSegundosDesde(unittest.TestCase):
    def test_none_devuelve_none(self):
        self.assertIsNone(eventos.segundos_desde(None))

    def test_texto_no_parseable_devuelve_none(self):
        self.assertIsNone(eventos.segundos_desde("no es una fecha"))

    def test_fecha_pasada_devuelve_segundos_positivos(self):
        from datetime import datetime, timedelta

        hace_un_minuto = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        resultado = eventos.segundos_desde(hace_un_minuto)
        self.assertIsNotNone(resultado)
        self.assertGreaterEqual(resultado, 55)


class PruebaResumenSeleccionAutomatica(BasePruebaEventos):
    def test_sin_eventos_todo_en_cero(self):
        resumen = eventos.resumen_seleccion_automatica()
        for nivel in eventos.NIVELES_CONFIANZA:
            self.assertEqual(resumen["por_confianza"][nivel]["sugeridas"], 0)
            self.assertIsNone(resumen["por_confianza"][nivel]["tasa_aceptacion"])
        self.assertEqual(resumen["total"]["sugeridas"], 0)
        self.assertIsNone(resumen["total"]["precision"])

    def test_sugerida_sin_decision_queda_pendiente(self):
        self._registrar(
            eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="alta",
        )
        resumen = eventos.resumen_seleccion_automatica()
        self.assertEqual(resumen["por_confianza"]["alta"]["sugeridas"], 1)
        self.assertEqual(resumen["por_confianza"]["alta"]["pendientes"], 1)
        self.assertIsNone(resumen["por_confianza"]["alta"]["precision"])
        self.assertEqual(resumen["por_confianza"]["alta"]["tasa_aceptacion"], 0.0)

    def test_item_agregado_manual_no_cuenta_como_sugerido(self):
        # origen='manual' o confianza_match=None -- nunca es una
        # "sugerencia automática", sin importar cuántos se agreguen.
        self._registrar(eventos.TIPO_ITEM_AGREGADO, origen="manual")
        self._registrar(eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match=None)
        resumen = eventos.resumen_seleccion_automatica()
        self.assertEqual(resumen["total"]["sugeridas"], 0)

    def test_precision_y_tasa_aceptacion_con_mezcla_de_decisiones(self):
        # Nivel "alta": 2 sugeridas, 1 aceptada, 1 pendiente.
        self._registrar(eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="alta")
        self._registrar(eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="alta")
        self._registrar(
            eventos.TIPO_SELECCION_ACEPTADA, confianza_match="alta",
            tiempo_hasta_decision_segundos=30,
        )

        # Nivel "baja": 2 sugeridas, 1 reemplazada, 1 eliminada -- 0% precisión.
        self._registrar(eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="baja")
        self._registrar(eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="baja")
        self._registrar(eventos.TIPO_SELECCION_REEMPLAZADA, confianza_match="baja")
        self._registrar(eventos.TIPO_SELECCION_ELIMINADA, confianza_match="baja")

        resumen = eventos.resumen_seleccion_automatica()

        alta = resumen["por_confianza"]["alta"]
        self.assertEqual(alta["sugeridas"], 2)
        self.assertEqual(alta["aceptadas"], 1)
        self.assertEqual(alta["pendientes"], 1)
        self.assertEqual(alta["tasa_aceptacion"], 0.5)
        self.assertEqual(alta["precision"], 1.0)  # de lo YA decidido, 1/1 fue aceptado
        self.assertEqual(alta["tiempo_promedio_aceptar_segundos"], 30.0)

        baja = resumen["por_confianza"]["baja"]
        self.assertEqual(baja["sugeridas"], 2)
        self.assertEqual(baja["pendientes"], 0)
        self.assertEqual(baja["precision"], 0.0)

        total = resumen["total"]
        self.assertEqual(total["sugeridas"], 4)
        self.assertEqual(total["aceptadas"], 1)
        self.assertEqual(total["precision"], round(1 / 3, 4))  # 1 aceptada de 3 decididas en total

    def test_filtro_de_dias_excluye_eventos_viejos(self):
        self._registrar(eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="alta")
        self._envejecer_ultimo_evento(dias=40)
        self._registrar(eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="alta")

        resumen_todo = eventos.resumen_seleccion_automatica()
        resumen_30_dias = eventos.resumen_seleccion_automatica(dias=30)

        self.assertEqual(resumen_todo["total"]["sugeridas"], 2)
        self.assertEqual(resumen_30_dias["total"]["sugeridas"], 1)


class PruebaMaterialesMasDificiles(BasePruebaEventos):
    def test_ordena_por_tasa_de_rechazo_descendente(self):
        # "puerta A": 4 sugeridas, 1 rechazada -- 25%.
        for _ in range(4):
            self._registrar(
                eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="media",
                texto_material="puerta A",
            )
        self._registrar(
            eventos.TIPO_SELECCION_ELIMINADA, origen="plano", confianza_match="media",
            texto_material="puerta A",
        )

        # "puerta B": 3 sugeridas, 2 rechazadas -- 66%.
        for _ in range(3):
            self._registrar(
                eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="media",
                texto_material="puerta B",
            )
        self._registrar(
            eventos.TIPO_SELECCION_ELIMINADA, origen="plano", confianza_match="media",
            texto_material="puerta B",
        )
        self._registrar(
            eventos.TIPO_SELECCION_REEMPLAZADA, origen="plano", confianza_match="media",
            texto_material="puerta B",
        )

        resultado = eventos.materiales_mas_dificiles(minimo_sugerencias=1)

        self.assertEqual(resultado[0]["texto_material"], "puerta B")
        self.assertEqual(resultado[0]["tasa_rechazo"], round(2 / 3, 4))
        self.assertEqual(resultado[1]["texto_material"], "puerta A")
        self.assertEqual(resultado[1]["tasa_rechazo"], 0.25)

    def test_minimo_sugerencias_descarta_muestras_chicas(self):
        # Una sola sugerencia, rechazada -- 100% de rechazo, pero con n=1
        # no debería aparecer si se pide un mínimo mayor.
        self._registrar(
            eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="baja",
            texto_material="material raro",
        )
        self._registrar(
            eventos.TIPO_SELECCION_ELIMINADA, origen="plano", confianza_match="baja",
            texto_material="material raro",
        )

        self.assertEqual(eventos.materiales_mas_dificiles(minimo_sugerencias=2), [])
        self.assertEqual(len(eventos.materiales_mas_dificiles(minimo_sugerencias=1)), 1)

    def test_respeta_el_limite(self):
        for i in range(5):
            texto = f"material {i}"
            self._registrar(
                eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="media",
                texto_material=texto,
            )
            self._registrar(
                eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="media",
                texto_material=texto,
            )

        resultado = eventos.materiales_mas_dificiles(limite=3, minimo_sugerencias=1)
        self.assertEqual(len(resultado), 3)


class PruebaCategoriasPeorDesempeno(BasePruebaEventos):
    def test_agrupa_por_categoria_en_vez_de_material(self):
        for _ in range(3):
            self._registrar(
                eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="media",
                categoria="Vidrios", texto_material="ventana grande",
            )
        self._registrar(
            eventos.TIPO_SELECCION_ELIMINADA, origen="plano", confianza_match="media",
            categoria="Vidrios", texto_material="ventana grande",
        )
        self._registrar(
            eventos.TIPO_SELECCION_ELIMINADA, origen="plano", confianza_match="media",
            categoria="Vidrios", texto_material="ventana grande",
        )

        resultado = eventos.categorias_peor_desempeno(minimo_sugerencias=1)
        self.assertEqual(resultado[0]["categoria"], "Vidrios")
        self.assertEqual(resultado[0]["sugeridas"], 3)
        self.assertEqual(resultado[0]["eliminadas"], 2)


if __name__ == "__main__":
    unittest.main()
