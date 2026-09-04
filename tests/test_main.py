"""Pruebas para GET /health y GET /version (api/main.py), ver
MASTER_ROADMAP.md, NOW #1: "no existe ningún mecanismo" para detectar un
backend roto o un catálogo vacío -- antes de esto, "/" respondía 200 sin
tocar la base de datos. Ampliado en Mission #003 (Production Health &
Deploy Guard) con los chequeos de schema/migraciones y disco
persistente.

No hay TestClient de FastAPI a propósito (requeriría httpx, no instalado)
-- salud() y version() son funciones Python normales sin `async def` ni
Depends() de request real, así que se llaman directo, mismo patrón que
tests/test_feedback.py y tests/test_routers_proyectos.py.

Reconciliado con las pruebas de GET /productos/{id}
(api/main.py::producto_por_id) -- RELEASE_CANDIDATE.md: reconstruye un
producto desde el backend a partir del id que ya usa /producto/{id} en el
frontend (base64url de url_producto, ver id_producto.py), para que un
link compartido, recargado, o sin sessionStorage siga funcionando. Mismo
patrón: la función de la ruta es una función Python normal, así que se
llama directo -- no hace falta un TestClient ni la dependencia de httpx."""

import base64
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import db
from fastapi import HTTPException

from api.main import producto_por_id, salud, version, _schema_listo, _disco_persistente_estado


def _crear_db_temporal(con_productos=True, cantidad_productos=0):
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    if con_productos:
        conexion = sqlite3.connect(archivo.name)
        conexion.execute("""
            CREATE TABLE productos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                precio REAL
            )
        """)
        for i in range(cantidad_productos):
            conexion.execute(
                "INSERT INTO productos (nombre, precio) VALUES (?, ?)",
                (f"Producto {i}", 1000),
            )
        conexion.commit()
        conexion.close()

    return archivo.name


def _agregar_tabla_migraciones(ruta_db, nombres_aplicados=()):
    """Mission #003: crea migraciones_aplicadas (mismo esquema que
    database/migraciones.py::_asegurar_tabla_seguimiento) con los nombres
    dados ya marcados como aplicados -- para controlar qué ve
    _schema_listo() sin depender de las migraciones reales del repo."""
    conexion = sqlite3.connect(ruta_db)
    conexion.execute("""
        CREATE TABLE migraciones_aplicadas (
            nombre TEXT PRIMARY KEY,
            fecha_aplicada TEXT NOT NULL
        )
    """)
    for nombre in nombres_aplicados:
        conexion.execute(
            "INSERT INTO migraciones_aplicadas (nombre, fecha_aplicada) VALUES (?, '2026-01-01 00:00:00')",
            (nombre,),
        )
    conexion.commit()
    conexion.close()


def _cuerpo(respuesta):
    return json.loads(respuesta.body)


class PruebaSaludBaseYCatalogoOk(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal(con_productos=True, cantidad_productos=3)
        self._patch = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch.start()
        # Mission #003: sin esto, _schema_listo() compara contra las
        # migraciones REALES registradas en database/migraciones.py (que
        # esta base de prueba nunca corrió) y reportaría 'pending' --
        # una lista vacía de migraciones "registradas" hace que el
        # chequeo de schema sea trivialmente 'ok' para este escenario,
        # que solo le interesa a database/catalog.
        self._patch_migraciones = mock.patch("api.main._MIGRACIONES", [])
        self._patch_migraciones.start()

    def tearDown(self):
        self._patch_migraciones.stop()
        self._patch.stop()
        os.remove(self.ruta_db)

    def test_devuelve_200_con_base_conectada_y_catalogo_no_vacio(self):
        respuesta = salud()

        self.assertEqual(respuesta.status_code, 200)
        cuerpo = _cuerpo(respuesta)
        self.assertEqual(cuerpo["status"], "ok")
        self.assertEqual(cuerpo["checks"]["database"], "ok")
        self.assertEqual(cuerpo["checks"]["catalog"], "ok")
        self.assertEqual(cuerpo["checks"]["schema"], "ok")


class PruebaSaludCatalogoVacio(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal(con_productos=True, cantidad_productos=0)
        self._patch = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch.start()
        self._patch_migraciones = mock.patch("api.main._MIGRACIONES", [])
        self._patch_migraciones.start()

    def tearDown(self):
        self._patch_migraciones.stop()
        self._patch.stop()
        os.remove(self.ruta_db)

    def test_devuelve_503_cuando_productos_tiene_cero_filas(self):
        respuesta = salud()

        self.assertEqual(respuesta.status_code, 503)
        cuerpo = _cuerpo(respuesta)
        self.assertEqual(cuerpo["status"], "degraded")
        self.assertEqual(cuerpo["checks"]["database"], "ok")
        self.assertEqual(cuerpo["checks"]["catalog"], "empty")


class PruebaSaludBaseInalcanzable(unittest.TestCase):
    def setUp(self):
        # Un directorio no es un archivo SQLite válido -- conectar() (o el
        # primer execute()) falla de forma confiable sin necesidad de
        # simular una desconexión de red real.
        self.directorio_temporal = tempfile.mkdtemp()
        self._patch = mock.patch.object(db, "BASE_DATOS", self.directorio_temporal)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.rmdir(self.directorio_temporal)

    def test_devuelve_503_cuando_la_base_no_se_puede_abrir(self):
        respuesta = salud()

        self.assertEqual(respuesta.status_code, 503)
        cuerpo = _cuerpo(respuesta)
        self.assertEqual(cuerpo["status"], "degraded")
        self.assertEqual(cuerpo["checks"]["database"], "error")

    def test_la_respuesta_no_filtra_detalles_internos(self):
        respuesta = salud()
        cuerpo = _cuerpo(respuesta)
        texto_crudo = respuesta.body.decode("utf-8")

        # Ni la ruta real de la base de datos (el directorio temporal usado
        # en esta prueba), ni palabras propias de un traceback/excepción de
        # sqlite3, ni nombres reales de migraciones, deben aparecer en el
        # cuerpo de la respuesta pública.
        self.assertNotIn(self.directorio_temporal, texto_crudo)
        for fragmento_prohibido in ("Traceback", "sqlite3", "Error:", "Exception", "agregar_"):
            self.assertNotIn(fragmento_prohibido, texto_crudo)

        # Mission #003: ahora cuatro claves de chequeo, todas con valores
        # de un conjunto fijo -- nada de mensajes libres, ni acá ni en
        # los dos chequeos nuevos.
        self.assertEqual(set(cuerpo["checks"].keys()), {"database", "catalog", "schema", "disk"})
        self.assertIn(cuerpo["checks"]["database"], {"ok", "error"})
        self.assertIn(cuerpo["checks"]["catalog"], {"ok", "empty", "unknown"})
        self.assertIn(cuerpo["checks"]["schema"], {"ok", "pending"})
        self.assertIn(cuerpo["checks"]["disk"], {"ok", "warning", "n/a"})


class PruebaSaludSchema(unittest.TestCase):
    """Mission #003: _schema_listo() compara TODAS las migraciones
    registradas (api.main._MIGRACIONES, mockeado acá con una lista
    chica y controlada) contra migraciones_aplicadas -- no solo si
    productos existe."""

    def setUp(self):
        self.ruta_db = _crear_db_temporal(con_productos=True, cantidad_productos=3)
        self._patch_db = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch_db.start()
        self._patch_migraciones = mock.patch("api.main._MIGRACIONES", [("m1", None), ("m2", None)])
        self._patch_migraciones.start()

    def tearDown(self):
        self._patch_migraciones.stop()
        self._patch_db.stop()
        os.remove(self.ruta_db)

    def test_ok_cuando_todas_las_registradas_estan_aplicadas(self):
        _agregar_tabla_migraciones(self.ruta_db, nombres_aplicados=["m1", "m2"])

        self.assertEqual(_schema_listo(), "ok")

        respuesta = salud()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(_cuerpo(respuesta)["checks"]["schema"], "ok")

    def test_pending_cuando_falta_una_migracion_registrada(self):
        _agregar_tabla_migraciones(self.ruta_db, nombres_aplicados=["m1"])  # falta "m2"

        self.assertEqual(_schema_listo(), "pending")

        respuesta = salud()
        self.assertEqual(respuesta.status_code, 503)
        cuerpo = _cuerpo(respuesta)
        self.assertEqual(cuerpo["status"], "degraded")
        self.assertEqual(cuerpo["checks"]["schema"], "pending")
        # database/catalog siguen "ok" -- schema es la única causa de
        # degradación en este escenario, no un side-effect de otra cosa.
        self.assertEqual(cuerpo["checks"]["database"], "ok")
        self.assertEqual(cuerpo["checks"]["catalog"], "ok")

    def test_pending_cuando_la_tabla_de_seguimiento_todavia_no_existe(self):
        # No se llama _agregar_tabla_migraciones -- migraciones_aplicadas
        # no existe todavía (arranque recién empezando, antes de que el
        # runner de migraciones alcance a crearla). migraciones_
        # completadas() ya está diseñada para no lanzar acá (devuelve
        # []) -- esto confirma que eso se traduce en 'pending', no en un
        # health check roto.
        self.assertEqual(_schema_listo(), "pending")

    def test_no_lanza_ante_un_fallo_inesperado_de_migraciones_completadas(self):
        # Segunda capa de defensa (ver docstring de _schema_listo): aunque
        # migraciones_completadas() ya no debería lanzar nunca, si por
        # algún cambio futuro lo hiciera, _schema_listo() igual debe
        # devolver 'pending', no propagar la excepción.
        with mock.patch("api.main._migraciones_completadas", side_effect=RuntimeError("boom")):
            self.assertEqual(_schema_listo(), "pending")


class PruebaSaludDisco(unittest.TestCase):
    """Mission #003: _disco_persistente_estado() -- ver el docstring de
    la función para el razonamiento completo. Estas pruebas son
    deliberadamente a nivel de unidad (llaman la función directo, sin
    pasar por salud()) porque no hace falta un archivo real bajo /data
    para probar la lógica de resolución de rutas -- Path.resolve() no
    exige que la ruta exista."""

    def _sin_render(self):
        parche = mock.patch.dict(os.environ, {}, clear=False)
        parche.start()
        os.environ.pop("RENDER_GIT_COMMIT", None)
        self.addCleanup(parche.stop)

    def _en_render(self):
        parche = mock.patch.dict(os.environ, {"RENDER_GIT_COMMIT": "abc1234"})
        parche.start()
        self.addCleanup(parche.stop)

    def test_n_a_fuera_de_render(self):
        self._sin_render()
        with mock.patch.object(db, "BASE_DATOS", "database/proyecta.db"):
            self.assertEqual(_disco_persistente_estado(), "n/a")

    def test_n_a_fuera_de_render_incluso_si_la_ruta_ya_es_data(self):
        # El chequeo de Render tiene prioridad -- ni se intenta resolver
        # la ruta si no estamos en Render, sin importar cuál sea.
        self._sin_render()
        with mock.patch.object(db, "BASE_DATOS", "/data/proyecta.db"):
            self.assertEqual(_disco_persistente_estado(), "n/a")

    def test_ok_en_render_con_ruta_dentro_del_montaje(self):
        self._en_render()
        with mock.patch.object(db, "BASE_DATOS", "/data/proyecta.db"):
            self.assertEqual(_disco_persistente_estado(), "ok")

    def test_ok_en_render_con_subdirectorio_dentro_del_montaje(self):
        self._en_render()
        with mock.patch.object(db, "BASE_DATOS", "/data/respaldos/proyecta.db"):
            self.assertEqual(_disco_persistente_estado(), "ok")

    def test_warning_en_render_con_ruta_relativa_local(self):
        self._en_render()
        with mock.patch.object(db, "BASE_DATOS", "database/proyecta.db"):
            self.assertEqual(_disco_persistente_estado(), "warning")

    def test_warning_boundary_prefijo_de_string_similar_no_se_acepta(self):
        # El caso exacto que un chequeo naive con .startswith("/data")
        # aceptaría por error -- "/data2" empieza con el mismo string
        # que "/data" pero NO es un subdirectorio real del montaje.
        self._en_render()
        with mock.patch.object(db, "BASE_DATOS", "/data2/proyecta.db"):
            self.assertEqual(_disco_persistente_estado(), "warning")

    def test_warning_boundary_prefijo_sin_separador(self):
        # Mismo caso límite, sin subdirectorio -- "/database" también
        # empieza con "/data" como string, tampoco es válido.
        self._en_render()
        with mock.patch.object(db, "BASE_DATOS", "/database/proyecta.db"):
            self.assertEqual(_disco_persistente_estado(), "warning")

    def test_warning_con_traversal_normalizado_hacia_afuera(self):
        # resolve() normaliza ".." antes de comparar -- esto termina
        # apuntando de verdad a /data2/proyecta.db, fuera del montaje.
        self._en_render()
        with mock.patch.object(db, "BASE_DATOS", "/data/../data2/proyecta.db"):
            self.assertEqual(_disco_persistente_estado(), "warning")

    def test_ok_con_traversal_normalizado_que_vuelve_adentro(self):
        # ".." que termina resolviendo DENTRO del montaje sigue siendo
        # válido -- confirma que el rechazo es por estar genuinamente
        # afuera, no por la sola presencia de "..".
        self._en_render()
        with mock.patch.object(db, "BASE_DATOS", "/data/sub/../proyecta.db"):
            self.assertEqual(_disco_persistente_estado(), "ok")

    def test_warning_no_afecta_el_status_code_general(self):
        # Integración completa: disk="warning" nunca debe bajar el
        # status a 503 por sí solo (ver Mission #003) -- database,
        # catalog y schema siguen determinando el status code.
        self._en_render()
        ruta_db = _crear_db_temporal(con_productos=True, cantidad_productos=3)
        try:
            with mock.patch.object(db, "BASE_DATOS", ruta_db), \
                 mock.patch("api.main._MIGRACIONES", []):
                respuesta = salud()
        finally:
            os.remove(ruta_db)

        self.assertEqual(respuesta.status_code, 200)
        cuerpo = _cuerpo(respuesta)
        self.assertEqual(cuerpo["status"], "ok")
        self.assertEqual(cuerpo["checks"]["disk"], "warning")


class PruebaVersion(unittest.TestCase):
    def test_no_toca_la_base_de_datos(self):
        # BASE_DATOS apunta a una ruta que no existe y no se crea -- si
        # version() tocara la base de alguna forma, esto lo haría fallar.
        ruta_inexistente = os.path.join(tempfile.gettempdir(), "no-existe-zentra-version-test.db")
        with mock.patch.object(db, "BASE_DATOS", ruta_inexistente):
            respuesta = version()

        self.assertFalse(os.path.exists(ruta_inexistente))
        self.assertIn("version", respuesta)
        self.assertIn("commit", respuesta)

    def test_usa_unknown_cuando_render_git_commit_no_esta_seteada(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RENDER_GIT_COMMIT", None)
            respuesta = version()

        self.assertEqual(respuesta["commit"], "unknown")

    def test_usa_render_git_commit_cuando_esta_seteada(self):
        with mock.patch.dict(os.environ, {"RENDER_GIT_COMMIT": "abc1234"}):
            respuesta = version()

        self.assertEqual(respuesta["commit"], "abc1234")

    def test_funciona_incluso_si_health_reportaria_degradado(self):
        # /version debe responder aunque la base esté inalcanzable --
        # identidad de despliegue es una preocupación distinta de salud.
        directorio_temporal = tempfile.mkdtemp()
        try:
            with mock.patch.object(db, "BASE_DATOS", directorio_temporal):
                respuesta_salud = salud()
                respuesta_version = version()

            self.assertEqual(respuesta_salud.status_code, 503)
            self.assertIn("version", respuesta_version)
        finally:
            os.rmdir(directorio_temporal)


def _id_de(url):
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def _crear_db_temporal_productos():
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    conexion = sqlite3.connect(archivo.name)
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
    conexion.execute(
        """
        CREATE TABLE familias_producto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor TEXT, categoria TEXT, firma_base TEXT,
            nombre_familia TEXT, fecha_calculo TEXT
        )
        """
    )
    conexion.commit()
    conexion.close()
    return archivo.name


def _insertar_producto(conexion, **campos):
    base = {
        "proveedor": "EPA", "id_proveedor": None, "sku": None, "nombre": None,
        "marca": None, "categoria": None, "subcategoria": None, "precio": 1000,
        "descripcion": None, "url_imagen": None, "url_producto": None,
        "peso": None, "imagenes_adicionales": None, "familia_id": None,
    }
    base.update(campos)
    columnas = ", ".join(base.keys())
    marcadores = ", ".join("?" for _ in base)
    conexion.execute(f"INSERT INTO productos ({columnas}) VALUES ({marcadores})", list(base.values()))


class PruebaProductoPorId(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal_productos()
        self._patch = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.remove(self.ruta_db)

    def _insertar(self, **campos):
        conexion = sqlite3.connect(self.ruta_db)
        _insertar_producto(conexion, id_proveedor="1", **campos)
        conexion.commit()
        conexion.close()

    def test_reconstruye_el_producto_a_partir_del_id_de_su_url(self):
        url = "https://www.epa.co.cr/producto/cemento-gris-42-5kg"
        self._insertar(
            nombre="Cemento Gris 42.5kg", precio=5000, categoria="Construcción",
            url_producto=url, url_imagen="https://img/cemento.jpg", marca="Holcim", sku="C001",
        )

        resultado = producto_por_id(_id_de(url))

        self.assertEqual(resultado["nombre"], "Cemento Gris 42.5kg")
        self.assertEqual(resultado["precio"], 5000)
        self.assertEqual(resultado["proveedor"], "EPA")
        self.assertEqual(resultado["id_proveedor"], "1")
        self.assertEqual(resultado["url_producto"], url)
        self.assertEqual(resultado["marca"], "Holcim")

    def test_id_que_no_es_base64_valido_da_404_no_500(self):
        with self.assertRaises(HTTPException) as contexto:
            producto_por_id("esto-no-es-un-id-valido-!!!")
        self.assertEqual(contexto.exception.status_code, 404)

    def test_url_bien_formada_pero_que_no_existe_en_el_catalogo_da_404(self):
        with self.assertRaises(HTTPException) as contexto:
            producto_por_id(_id_de("https://www.epa.co.cr/producto/no-existe"))
        self.assertEqual(contexto.exception.status_code, 404)

    def test_incluye_familia_cuando_el_producto_tiene_una(self):
        conexion = sqlite3.connect(self.ruta_db)
        conexion.execute(
            "INSERT INTO familias_producto (id, proveedor, categoria, firma_base, nombre_familia) "
            "VALUES (1, 'EPA', 'Pinturas', 'pintura blanca', 'Pintura Blanca')"
        )
        conexion.commit()
        conexion.close()

        url = "https://www.epa.co.cr/producto/pintura-blanca-galon"
        self._insertar(
            nombre="Pintura Blanca Galón", categoria="Pinturas",
            url_producto=url, familia_id=1,
        )

        resultado = producto_por_id(_id_de(url))

        self.assertEqual(resultado["familia_id"], 1)
        self.assertEqual(resultado["nombre_familia"], "Pintura Blanca")

    def test_url_con_caracteres_no_ascii_hace_ida_y_vuelta(self):
        url = "https://tienda.com/prod?x=áéí&y=ñ"
        self._insertar(nombre="Producto con acentos", url_producto=url)

        resultado = producto_por_id(_id_de(url))

        self.assertEqual(resultado["url_producto"], url)


if __name__ == "__main__":
    unittest.main()
