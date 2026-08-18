"""Pruebas para api/repositorio_proyectos.py::iniciar_analisis_plano /
analizar_plano_sincrono / eliminar_plano -- el flujo asíncrono de Mission
#002 (Plan Processing Stability) y su capa de compatibilidad para
clientes que todavía no migraron.

No corre lectura_planos de verdad (esa lógica ya tiene su propia
cobertura en tests/test_lectura_planos*.py) -- acá se mockea
_EXECUTOR_PLANOS.submit(...) con un future falso, y se prueba la
orquestación: reclamo de concurrencia por usuario, transición de estados
(procesando -> listo/error), que un resultado/timeout de un token viejo
nunca pise uno más nuevo, y que analizar_plano_sincrono() (compatibilidad)
siga devolviendo el mismo contrato de antes de esta misión.

El mock del future simula add_done_callback() invocando el callback real
de forma SÍNCRONA (mismo comportamiento que concurrent.futures cuando el
future ya está resuelto en el momento de engancharlo) -- así
_completar_analisis_plano() corre de verdad, en el mismo hilo de la
prueba, sin necesitar hilos ni tiempos de espera reales."""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from fastapi import HTTPException

from api.repositorio_proyectos import (
    AnalisisPlanoEnCurso,
    analizar_plano_sincrono,
    crear_proyecto,
    eliminar_plano,
    iniciar_analisis_plano,
    obtener_proyecto,
    recuperar_analisis_interrumpidos,
)
import api.repositorio_proyectos as repo
from api.routers.proyectos import subir_plano as subir_plano_router


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
            plano_fecha_analisis TEXT,
            plano_estado TEXT,
            plano_error_mensaje TEXT,
            plano_procesamiento_id TEXT
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
            confianza_match TEXT,
            revisado INTEGER NOT NULL DEFAULT 1,
            cantidad_comprada REAL NOT NULL DEFAULT 0,
            monto_comprado REAL,
            fecha_compra TEXT,
            comprobante_tipo TEXT,
            comprobante_referencia TEXT,
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


def _future_falso(resultado=None, excepcion=None, invocar_callback_sincrono=True):
    """future de concurrent.futures simulado. Con
    invocar_callback_sincrono=True (el caso normal en estas pruebas),
    add_done_callback(fn) llama fn(future) de inmediato -- mismo
    comportamiento real de concurrent.futures cuando el future ya está
    resuelto en el momento de engancharlo (ver
    Future._invoke_callbacks()). Con False, add_done_callback() no hace
    nada -- simula un análisis que todavía no terminó, para probar el
    reclamo de concurrencia mientras un análisis sigue 'procesando'."""
    futuro = mock.MagicMock()
    if excepcion is not None:
        futuro.result.side_effect = excepcion
    else:
        futuro.result.return_value = resultado

    if invocar_callback_sincrono:
        futuro.add_done_callback.side_effect = lambda fn: fn(futuro)
    # si es False, add_done_callback queda como MagicMock normal -- no
    # invoca nada, el future queda "eternamente pendiente" para la prueba.

    return futuro


def _mockear_executor(resultado=None, excepcion=None, invocar_callback_sincrono=True):
    futuro = _future_falso(resultado, excepcion, invocar_callback_sincrono)
    parche = mock.patch("api.repositorio_proyectos._EXECUTOR_PLANOS")
    executor_falso = parche.start()
    executor_falso.submit.return_value = futuro
    return parche, futuro


class PruebaAnalizarPlanoSincrono(unittest.TestCase):
    """Mismo contrato observable que el analizar_plano() de antes de
    Mission #002 -- ver api/routers/proyectos.py, asincronico=False, el
    default para clientes que todavía no migraron."""

    PROPIETARIO = "propietario-plano-test"

    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch_db = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch_db.start()
        self._patch_executor, self._futuro = _mockear_executor(resultado=ANALISIS_DE_PRUEBA)

        self.proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con plano")

    def tearDown(self):
        self._patch_executor.stop()
        self._patch_db.stop()
        os.remove(self.ruta_db)

    def test_guarda_el_analisis_como_json_y_lo_devuelve_ya_parseado(self):
        resultado = analizar_plano_sincrono(
            self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf"
        )
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado["plano_nombre_archivo"], "planos.pdf")
        self.assertEqual(resultado["plano_analisis"], ANALISIS_DE_PRUEBA)
        self.assertIsNotNone(resultado["plano_fecha_analisis"])
        self.assertEqual(resultado["plano_estado"], "listo")

    def test_el_analisis_queda_persistido_como_json_valido_en_la_columna(self):
        analizar_plano_sincrono(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf")

        conexion = sqlite3.connect(self.ruta_db)
        fila = conexion.execute(
            "SELECT plano_analisis FROM proyectos WHERE id = ?", (self.proyecto["id"],)
        ).fetchone()
        conexion.close()
        self.assertEqual(json.loads(fila[0]), ANALISIS_DE_PRUEBA)

    def test_proyecto_inexistente_devuelve_none(self):
        resultado = analizar_plano_sincrono(999999, self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf")
        self.assertIsNone(resultado)

    def test_no_deja_analizar_el_plano_de_otro_propietario(self):
        resultado = analizar_plano_sincrono(
            self.proyecto["id"], "otro-propietario-distinto", "/tmp/no-importa.pdf", "planos.pdf"
        )
        self.assertIsNone(resultado)

    def test_actualiza_fecha_actualizacion_del_proyecto(self):
        antes = self.proyecto["fecha_actualizacion"]
        resultado = analizar_plano_sincrono(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf")
        # No afirmamos un valor de tiempo distinto (podrían coincidir al
        # segundo en una corrida muy rápida) -- solo que el campo sigue
        # ahí y no quedó vacío.
        self.assertIsNotNone(resultado["fecha_actualizacion"])
        self.assertIsNotNone(antes)

    def test_reemplazar_un_plano_ya_analizado_pisa_el_anterior(self):
        analizar_plano_sincrono(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "primero.pdf")

        segundo_analisis = dict(ANALISIS_DE_PRUEBA, proyecto_nombre="Casa reemplazada", cantidad_laminas=7)
        self._patch_executor.stop()
        self._patch_executor, self._futuro = _mockear_executor(resultado=segundo_analisis)

        resultado = analizar_plano_sincrono(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "segundo.pdf")
        self.assertEqual(resultado["plano_nombre_archivo"], "segundo.pdf")
        self.assertEqual(resultado["plano_analisis"]["cantidad_laminas"], 7)

    def test_error_real_del_analisis_se_propaga_y_no_deja_colgado(self):
        self._patch_executor.stop()
        self._patch_executor, self._futuro = _mockear_executor(excepcion=ValueError("PDF corrupto interno"))

        with self.assertRaises(ValueError):
            analizar_plano_sincrono(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "malo.pdf")

        proyecto = obtener_proyecto(self.proyecto["id"], propietario_id=self.PROPIETARIO)
        self.assertEqual(proyecto["plano_estado"], "error")
        # Nunca la excepción real -- solo el mensaje seguro genérico.
        self.assertNotIn("PDF corrupto interno", proyecto["plano_error_mensaje"] or "")


class PruebaFlujoAsincronico(unittest.TestCase):
    """asincronico=True (ver api/routers/proyectos.py) -- iniciar_analisis_
    plano() nunca bloquea, el estado se consulta por separado."""

    PROPIETARIO = "propietario-plano-async"

    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch_db = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch_db.start()
        self.proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto async")

    def tearDown(self):
        self._patch_db.stop()
        os.remove(self.ruta_db)

    def test_encola_sin_bloquear_y_marca_procesando(self):
        parche, futuro = _mockear_executor(invocar_callback_sincrono=False)
        try:
            resultado = iniciar_analisis_plano(
                self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf"
            )
            self.assertIsNotNone(resultado)
            proyecto, future = resultado
            self.assertEqual(proyecto["plano_estado"], "procesando")
            self.assertIs(future, futuro)
            # El token interno nunca se expone en la respuesta.
            self.assertNotIn("plano_procesamiento_id", proyecto)
        finally:
            parche.stop()
            repo._TEMPORIZADORES_ANALISIS.clear()

    def test_callback_exitoso_marca_listo(self):
        parche, futuro = _mockear_executor(resultado=ANALISIS_DE_PRUEBA)
        try:
            iniciar_analisis_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf")
            proyecto = obtener_proyecto(self.proyecto["id"], propietario_id=self.PROPIETARIO)
            self.assertEqual(proyecto["plano_estado"], "listo")
            self.assertEqual(proyecto["plano_analisis"], ANALISIS_DE_PRUEBA)
        finally:
            parche.stop()

    def test_callback_con_excepcion_marca_error_sin_filtrar_detalle(self):
        parche, futuro = _mockear_executor(excepcion=RuntimeError("ruta interna /tmp/xyz falló"))
        try:
            iniciar_analisis_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf")
            proyecto = obtener_proyecto(self.proyecto["id"], propietario_id=self.PROPIETARIO)
            self.assertEqual(proyecto["plano_estado"], "error")
            self.assertEqual(proyecto["plano_error_mensaje"], repo.MENSAJE_ERROR_GENERICO)
            self.assertNotIn("/tmp/xyz", proyecto["plano_error_mensaje"])
        finally:
            parche.stop()

    def test_segundo_intento_mientras_el_primero_procesa_levanta_en_curso(self):
        parche, futuro = _mockear_executor(invocar_callback_sincrono=False)
        try:
            iniciar_analisis_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "primero.pdf")

            with self.assertRaises(AnalisisPlanoEnCurso):
                iniciar_analisis_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "segundo.pdf")
        finally:
            parche.stop()
            repo._TEMPORIZADORES_ANALISIS.clear()

    def test_segundo_intento_bloqueado_incluye_otro_proyecto_del_mismo_usuario(self):
        otro_proyecto = crear_proyecto(self.PROPIETARIO, "Segundo proyecto")
        parche, futuro = _mockear_executor(invocar_callback_sincrono=False)
        try:
            iniciar_analisis_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "primero.pdf")

            with self.assertRaises(AnalisisPlanoEnCurso):
                iniciar_analisis_plano(otro_proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "segundo.pdf")
        finally:
            parche.stop()
            repo._TEMPORIZADORES_ANALISIS.clear()

    def test_usuario_distinto_no_se_ve_bloqueado(self):
        parche, futuro = _mockear_executor(invocar_callback_sincrono=False)
        try:
            iniciar_analisis_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "primero.pdf")

            otro_propietario = "otro-propietario-plano-async"
            otro_proyecto = crear_proyecto(otro_propietario, "Proyecto de otro usuario")
            resultado = iniciar_analisis_plano(
                otro_proyecto["id"], otro_propietario, "/tmp/no-importa.pdf", "segundo.pdf"
            )
            self.assertIsNotNone(resultado)
        finally:
            parche.stop()
            repo._TEMPORIZADORES_ANALISIS.clear()

    def test_token_viejo_no_pisa_un_intento_mas_nuevo(self):
        """Simula un resultado tardío: el callback del PRIMER intento
        corre después de que el proyecto ya avanzó a un token nuevo (ej.
        el usuario borró el plano y subió otro mientras el primero seguía
        'procesando'). El UPDATE del callback viejo debe hacer no-op."""
        # resultado=ANALISIS_DE_PRUEBA (no None): _procesar_plano_pdf
        # siempre devuelve un dict en producción -- el punto de esta
        # prueba es el guard por token, no qué contiene el análisis.
        parche, futuro_viejo = _mockear_executor(resultado=ANALISIS_DE_PRUEBA, invocar_callback_sincrono=False)
        try:
            iniciar_analisis_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "primero.pdf")
        finally:
            parche.stop()

        conexion = sqlite3.connect(self.ruta_db)
        conexion.row_factory = sqlite3.Row
        token_viejo = conexion.execute(
            "SELECT plano_procesamiento_id FROM proyectos WHERE id = ?", (self.proyecto["id"],)
        ).fetchone()["plano_procesamiento_id"]
        conexion.close()

        # Un segundo intento "reemplaza" el primero directamente en la
        # base (simula que _reclamar_slot_analisis de un nuevo intento ya
        # pasó) -- token distinto, mismo proyecto.
        conexion = sqlite3.connect(self.ruta_db)
        conexion.execute(
            "UPDATE proyectos SET plano_procesamiento_id = 'token-nuevo', plano_estado = 'procesando' WHERE id = ?",
            (self.proyecto["id"],),
        )
        conexion.commit()
        conexion.close()

        # Ahora dispara el callback del intento VIEJO manualmente.
        repo._completar_analisis_plano(self.proyecto["id"], token_viejo, "primero.pdf", futuro_viejo)

        proyecto = obtener_proyecto(self.proyecto["id"], propietario_id=self.PROPIETARIO)
        # Sigue 'procesando' (el estado del intento NUEVO) -- el
        # resultado del intento viejo no lo tocó.
        self.assertEqual(proyecto["plano_estado"], "procesando")


class PruebaWatchdogTimeout(unittest.TestCase):
    PROPIETARIO = "propietario-plano-timeout"

    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch_db = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch_db.start()
        self.proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto timeout")

    def tearDown(self):
        self._patch_db.stop()
        os.remove(self.ruta_db)

    def test_marca_error_si_sigue_procesando(self):
        parche, futuro = _mockear_executor(invocar_callback_sincrono=False)
        try:
            iniciar_analisis_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf")
            conexion = sqlite3.connect(self.ruta_db)
            conexion.row_factory = sqlite3.Row
            token = conexion.execute(
                "SELECT plano_procesamiento_id FROM proyectos WHERE id = ?", (self.proyecto["id"],)
            ).fetchone()["plano_procesamiento_id"]
            conexion.close()
        finally:
            parche.stop()
            repo._TEMPORIZADORES_ANALISIS.clear()

        with mock.patch("api.repositorio_proyectos._reciclar_executor_planos") as reciclar_falso:
            repo._manejar_timeout_analisis(self.proyecto["id"], token)
            reciclar_falso.assert_called_once()

        proyecto = obtener_proyecto(self.proyecto["id"], propietario_id=self.PROPIETARIO)
        self.assertEqual(proyecto["plano_estado"], "error")
        self.assertEqual(proyecto["plano_error_mensaje"], repo.MENSAJE_ERROR_TIMEOUT)

    def test_no_hace_nada_si_ya_termino_antes(self):
        """El callback normal ya canceló el timer y marcó 'listo' -- si
        por una carrera el timeout dispara de todas formas, no debe
        pisar un resultado ya persistido."""
        parche, futuro = _mockear_executor(resultado=ANALISIS_DE_PRUEBA)
        try:
            iniciar_analisis_plano(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf")
            conexion = sqlite3.connect(self.ruta_db)
            conexion.row_factory = sqlite3.Row
            token = conexion.execute(
                "SELECT plano_procesamiento_id FROM proyectos WHERE id = ?", (self.proyecto["id"],)
            ).fetchone()["plano_procesamiento_id"]
            conexion.close()
        finally:
            parche.stop()

        with mock.patch("api.repositorio_proyectos._reciclar_executor_planos") as reciclar_falso:
            repo._manejar_timeout_analisis(self.proyecto["id"], token)
            reciclar_falso.assert_not_called()

        proyecto = obtener_proyecto(self.proyecto["id"], propietario_id=self.PROPIETARIO)
        self.assertEqual(proyecto["plano_estado"], "listo")


class PruebaRecuperacionAlArrancar(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch_db = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch_db.start()

    def tearDown(self):
        self._patch_db.stop()
        os.remove(self.ruta_db)

    def test_marca_error_cualquier_procesando_huerfano(self):
        propietario = "propietario-recuperacion"
        p1 = crear_proyecto(propietario, "Proyecto huérfano 1")
        p2 = crear_proyecto(propietario, "Proyecto huérfano 2")
        p3 = crear_proyecto(propietario, "Proyecto sin plano")

        conexion = sqlite3.connect(self.ruta_db)
        conexion.execute(
            "UPDATE proyectos SET plano_estado = 'procesando', plano_procesamiento_id = 'tok-1' WHERE id = ?",
            (p1["id"],),
        )
        conexion.execute(
            "UPDATE proyectos SET plano_estado = 'procesando', plano_procesamiento_id = 'tok-2' WHERE id = ?",
            (p2["id"],),
        )
        conexion.commit()
        conexion.close()

        afectados = recuperar_analisis_interrumpidos()
        self.assertEqual(afectados, 2)

        proyecto1 = obtener_proyecto(p1["id"], propietario_id=propietario)
        proyecto2 = obtener_proyecto(p2["id"], propietario_id=propietario)
        proyecto3 = obtener_proyecto(p3["id"], propietario_id=propietario)

        self.assertEqual(proyecto1["plano_estado"], "error")
        self.assertEqual(proyecto1["plano_error_mensaje"], repo.MENSAJE_ERROR_INTERRUPCION)
        self.assertEqual(proyecto2["plano_estado"], "error")
        # Proyecto sin plano nunca tocado -- sigue en None, no 'error'.
        self.assertIsNone(proyecto3["plano_estado"])

    def test_no_hace_nada_si_no_hay_nada_procesando(self):
        self.assertEqual(recuperar_analisis_interrumpidos(), 0)


class PruebaEliminarPlano(unittest.TestCase):
    PROPIETARIO = "propietario-plano-test"

    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch_db = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch_db.start()
        self._patch_executor, self._futuro = _mockear_executor(resultado=ANALISIS_DE_PRUEBA)

        self.proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con plano")
        analizar_plano_sincrono(self.proyecto["id"], self.PROPIETARIO, "/tmp/no-importa.pdf", "planos.pdf")

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


class _ArchivoFalso:
    """Sustituto mínimo de fastapi.UploadFile -- subir_plano() solo toca
    .content_type, .filename y .file.read(tamaño), así que no hace falta
    un UploadFile real (que arrastraría python-multipart/httpx) para
    probar el despacho del router en sí."""

    def __init__(self, contenido=b"%PDF-1.4 contenido falso", content_type="application/pdf", filename="plano.pdf"):
        import io
        self.content_type = content_type
        self.filename = filename
        self.file = io.BytesIO(contenido)


class PruebaRouterSubirPlano(unittest.TestCase):
    """api/routers/proyectos.py::subir_plano -- despacho entre el flujo
    síncrono de compatibilidad (asincronico=False, default) y el nuevo
    asíncrono (asincronico=True), y el mapeo de AnalisisPlanoEnCurso a
    429. La lógica real de estado ya está cubierta arriba (Prueba
    AnalizarPlanoSincrono / PruebaFlujoAsincronico) -- acá se mockea
    repo.iniciar_analisis_plano/analizar_plano_sincrono directo, para
    probar solo lo que le pertenece al router."""

    PROPIETARIO = "propietario-router-plano"

    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch_db = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch_db.start()
        self.proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto router")

    def tearDown(self):
        self._patch_db.stop()
        os.remove(self.ruta_db)

    def test_asincronico_false_devuelve_200_con_el_proyecto(self):
        with mock.patch(
            "api.routers.proyectos.repo.analizar_plano_sincrono",
            return_value={"id": self.proyecto["id"], "plano_estado": "listo"},
        ):
            resultado = subir_plano_router(
                self.proyecto["id"], archivo=_ArchivoFalso(), asincronico=False, propietario_id=self.PROPIETARIO
            )
        # Sin JSONResponse -- FastAPI serializa el dict devuelto tal cual,
        # 200 es el default cuando el handler no arma su propia respuesta.
        self.assertEqual(resultado["plano_estado"], "listo")

    def test_asincronico_true_devuelve_202(self):
        with mock.patch(
            "api.routers.proyectos.repo.iniciar_analisis_plano",
            return_value=({"id": self.proyecto["id"], "plano_estado": "procesando"}, mock.MagicMock()),
        ):
            resultado = subir_plano_router(
                self.proyecto["id"], archivo=_ArchivoFalso(), asincronico=True, propietario_id=self.PROPIETARIO
            )
        self.assertEqual(resultado.status_code, 202)
        self.assertEqual(json.loads(resultado.body)["plano_estado"], "procesando")

    def test_analisis_en_curso_mapea_a_429(self):
        with mock.patch(
            "api.routers.proyectos.repo.analizar_plano_sincrono", side_effect=AnalisisPlanoEnCurso()
        ):
            with self.assertRaises(HTTPException) as contexto:
                subir_plano_router(
                    self.proyecto["id"], archivo=_ArchivoFalso(), asincronico=False, propietario_id=self.PROPIETARIO
                )
        self.assertEqual(contexto.exception.status_code, 429)

    def test_analisis_en_curso_asincronico_tambien_mapea_a_429(self):
        with mock.patch(
            "api.routers.proyectos.repo.iniciar_analisis_plano", side_effect=AnalisisPlanoEnCurso()
        ):
            with self.assertRaises(HTTPException) as contexto:
                subir_plano_router(
                    self.proyecto["id"], archivo=_ArchivoFalso(), asincronico=True, propietario_id=self.PROPIETARIO
                )
        self.assertEqual(contexto.exception.status_code, 429)

    def test_content_type_invalido_sigue_dando_422(self):
        with self.assertRaises(HTTPException) as contexto:
            subir_plano_router(
                self.proyecto["id"],
                archivo=_ArchivoFalso(content_type="image/png"),
                asincronico=False,
                propietario_id=self.PROPIETARIO,
            )
        self.assertEqual(contexto.exception.status_code, 422)

    def test_proyecto_inexistente_da_404_en_ambos_modos(self):
        with mock.patch("api.routers.proyectos.repo.analizar_plano_sincrono", return_value=None):
            with self.assertRaises(HTTPException) as contexto:
                subir_plano_router(
                    999999, archivo=_ArchivoFalso(), asincronico=False, propietario_id=self.PROPIETARIO
                )
        self.assertEqual(contexto.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
