"""Pruebas para busqueda.py -- en particular, regresión de los dos falsos
negativos reales de BUSCADOR_AUDITORIA.md:

- "bloque" no encontraba los bloques de concreto reales del catálogo
  (listados como "Block", préstamo del inglés) -- GRUPOS_VOCABULARIO_MATERIALES.
- "perlín" devolvía cero resultados -- el catálogo lo llama "Perfil".

Usa una base SQLite temporal con FTS5 real (mismo patrón que
tests/test_similares.py, nunca contra database/proyecta.db).
"""

import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from busqueda import (
    GRUPOS_VOCABULARIO_MATERIALES,
    VARIANTE_A_GRUPO,
    normalizar_texto,
    tokenizar,
)


class PruebaNormalizarYTokenizar(unittest.TestCase):
    def test_normalizar_quita_acentos_y_minuscula(self):
        self.assertEqual(normalizar_texto("Perlín"), "perlin")
        self.assertEqual(normalizar_texto("CERÁMICA"), "ceramica")

    def test_tokenizar_aplica_sinonimos_de_unidades(self):
        self.assertEqual(tokenizar(normalizar_texto("5 metros")), ["5", "m"])

    def test_tokenizar_quita_stopwords_cortas(self):
        self.assertNotIn("de", tokenizar(normalizar_texto("tubo de pvc")))


class PruebaGruposVocabularioMateriales(unittest.TestCase):
    """Sinónimos de vocabulario real (no morfológicos) -- confirmados
    contra el catálogo real, ver BUSCADOR_AUDITORIA.md."""

    def test_bloque_y_block_son_el_mismo_grupo(self):
        grupo = VARIANTE_A_GRUPO.get("bloque")
        self.assertIsNotNone(grupo)
        self.assertIn("block", grupo)
        self.assertEqual(grupo, VARIANTE_A_GRUPO.get("block"))

    def test_perlin_y_perfil_son_el_mismo_grupo(self):
        grupo = VARIANTE_A_GRUPO.get("perlin")
        self.assertIsNotNone(grupo)
        self.assertIn("perfil", grupo)
        self.assertEqual(grupo, VARIANTE_A_GRUPO.get("perfil"))

    def test_grupos_no_se_solapan_entre_si(self):
        # Cada palabra pertenece a un único grupo de sinónimos -- si se
        # solapan, VARIANTE_A_GRUPO se pisa silenciosamente al construirse.
        vistas = set()
        for grupo in GRUPOS_VOCABULARIO_MATERIALES:
            self.assertTrue(vistas.isdisjoint(grupo), f"grupo solapado: {grupo}")
            vistas |= grupo


def _crear_db_temporal():
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
    conexion.execute(
        """
        CREATE VIRTUAL TABLE productos_fts USING fts5(
            nombre, categoria, subcategoria, content=''
        )
        """
    )
    conexion.commit()
    conexion.close()
    return archivo.name


def _crear_db_temporal_como_produccion():
    """Reproduce el detalle que disparó la regresión: `id INT` sin índice."""
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    conexion = sqlite3.connect(archivo.name)
    conexion.executescript(
        """
        CREATE TABLE productos (
            id INT, proveedor TEXT, id_proveedor TEXT, sku TEXT, nombre TEXT,
            marca TEXT, categoria TEXT, subcategoria TEXT, precio REAL,
            descripcion TEXT, url_imagen TEXT, url_producto TEXT,
            peso TEXT, imagenes_adicionales TEXT, familia_id INTEGER
        );
        CREATE TABLE familias_producto (
            id INTEGER PRIMARY KEY, proveedor TEXT, categoria TEXT,
            firma_base TEXT, nombre_familia TEXT, fecha_calculo TEXT
        );
        CREATE VIRTUAL TABLE productos_fts USING fts5(
            nombre, categoria, subcategoria, content=''
        );
        """
    )
    conexion.close()
    return archivo.name


def _insertar(conexion, **campos):
    base = {
        "proveedor": None, "id_proveedor": None, "sku": None, "nombre": None,
        "marca": None, "categoria": None, "subcategoria": None, "precio": 1000,
        "descripcion": None, "url_imagen": None, "url_producto": None,
        "peso": None, "imagenes_adicionales": None, "familia_id": None,
    }
    base.update(campos)
    columnas = ", ".join(base.keys())
    marcadores = ", ".join("?" for _ in base)
    conexion.execute(
        f"INSERT INTO productos ({columnas}) VALUES ({marcadores})", list(base.values())
    )


class BasePruebaBuscarFts(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal()

    def tearDown(self):
        os.remove(self.ruta_db)

    def _cargar_y_reconstruir_indice(self, filas):
        conexion = sqlite3.connect(self.ruta_db)
        for fila in filas:
            _insertar(conexion, **fila)
        conexion.commit()
        conexion.close()

        import db
        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            import busqueda
            busqueda.reconstruir_indice()

    def _buscar(self, consulta, **kwargs):
        import db
        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            import busqueda
            return busqueda.buscar_fts(consulta, **kwargs)


def _buscar_con_join_anterior(ruta_db, consulta, limite=50):
    """Consulta anterior, conservada solo como oráculo de regresión/benchmark."""
    import busqueda

    tokens = busqueda.tokenizar(busqueda.normalizar_texto(consulta))
    consulta_fts = " AND ".join(busqueda._condicion_fts(token) for token in tokens)
    conexion = sqlite3.connect(ruta_db)
    conexion.row_factory = sqlite3.Row
    filas = conexion.execute(
        f"""
        SELECT
            p.nombre, p.precio, p.categoria, p.proveedor,
            p.id_proveedor, p.url_producto, p.url_imagen,
            p.marca, p.sku, p.subcategoria, p.descripcion,
            p.peso, p.imagenes_adicionales,
            p.familia_id, f.nombre_familia,
            bm25(productos_fts, {busqueda.PESO_NOMBRE},
                 {busqueda.PESO_CATEGORIA},
                 {busqueda.PESO_SUBCATEGORIA}) AS puntaje
        FROM productos_fts
        JOIN productos p ON p.id = productos_fts.rowid
        LEFT JOIN familias_producto f ON f.id = p.familia_id
        WHERE productos_fts MATCH ?
        ORDER BY puntaje
        LIMIT ?
        """,
        (consulta_fts, limite),
    ).fetchall()
    conexion.close()
    return [dict(fila) for fila in filas]


class PruebaRecuperacionDosFases(BasePruebaBuscarFts):
    def test_preserva_resultados_campos_y_orden_bm25(self):
        self._cargar_y_reconstruir_indice([
            {
                "proveedor": "EPA", "id_proveedor": "1", "sku": "SKU-1",
                "nombre": "Pintura blanca interior", "marca": "Marca A",
                "categoria": "Pinturas", "subcategoria": "Interior",
                "precio": 12000, "descripcion": "Acabado mate",
            },
            {
                "proveedor": "Lagar", "id_proveedor": "2", "sku": "SKU-2",
                "nombre": "Pintura para baño", "marca": "Marca B",
                "categoria": "Pinturas", "subcategoria": "Especialidad",
                "precio": 15000, "descripcion": "Resistente a humedad",
            },
            {
                "proveedor": "EPA", "id_proveedor": "3", "sku": "SKU-3",
                "nombre": "Rodillo para pintura", "marca": "Marca C",
                "categoria": "Accesorios", "subcategoria": "Aplicadores",
                "precio": 3000,
            },
        ])

        esperado = _buscar_con_join_anterior(self.ruta_db, "pintura", limite=3)
        actual = self._buscar("pintura", limite=3)

        self.assertEqual(actual, esperado)

    def test_filtros_se_aplican_antes_del_limite_final(self):
        self._cargar_y_reconstruir_indice([
            {
                "proveedor": "EPA", "id_proveedor": "1",
                "nombre": "Pintura pintura pintura sellador",
                "categoria": "Pinturas",
            },
            {
                "proveedor": "EPA", "id_proveedor": "2",
                "nombre": "Pintura antihongos para baño",
                "categoria": "Pinturas",
            },
            {
                "proveedor": "EPA", "id_proveedor": "3",
                "nombre": "Pintura para manualidades",
                "categoria": "Hogar",
            },
        ])

        resultados = self._buscar(
            "pintura",
            limite=1,
            categorias_permitidas=["Pinturas"],
            exclusiones=["sellador"],
        )

        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0]["nombre"], "Pintura antihongos para baño")


class PruebaRendimientoRecuperacionDosFases(unittest.TestCase):
    def test_evitar_join_fts_por_fila_en_esquema_sin_indice(self):
        ruta_db = _crear_db_temporal_como_produccion()
        self.addCleanup(os.remove, ruta_db)
        conexion = sqlite3.connect(ruta_db)
        productos = []
        entradas_fts = []
        for producto_id in range(1, 12001):
            coincide = producto_id <= 2500
            nombre = f"Pintura interior {producto_id}" if coincide else f"Producto {producto_id}"
            categoria = "Pinturas" if coincide else "Ferretería"
            productos.append((producto_id, "Proveedor", str(producto_id), nombre, categoria, 1000.0))
            entradas_fts.append((producto_id, nombre, categoria, None))
        conexion.executemany(
            """
            INSERT INTO productos
                (id, proveedor, id_proveedor, nombre, categoria, precio)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            productos,
        )
        conexion.executemany(
            "INSERT INTO productos_fts(rowid, nombre, categoria, subcategoria) VALUES (?, ?, ?, ?)",
            entradas_fts,
        )
        conexion.commit()
        conexion.close()

        inicio_anterior = time.perf_counter()
        resultado_anterior = _buscar_con_join_anterior(ruta_db, "pintura", limite=300)
        duracion_anterior = time.perf_counter() - inicio_anterior

        import db
        import busqueda
        inicio_nuevo = time.perf_counter()
        with mock.patch.object(db, "BASE_DATOS", ruta_db):
            resultado_nuevo = busqueda.buscar_fts("pintura", limite=300)
        duracion_nueva = time.perf_counter() - inicio_nuevo

        self.assertEqual(resultado_nuevo, resultado_anterior)
        self.assertLess(
            duracion_nueva,
            duracion_anterior * 0.4,
            f"dos fases={duracion_nueva:.4f}s, join anterior={duracion_anterior:.4f}s",
        )


class PruebaRegresionSinonimosVocabulario(BasePruebaBuscarFts):
    def test_bloque_encuentra_block_ingles(self):
        self._cargar_y_reconstruir_indice([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Block PC clase A 20 x 20 x 40 cm",
             "categoria": "Construccion"},
            {"proveedor": "Carbone Store", "id_proveedor": "2",
             "nombre": "Bloque De Aluminio 300 Mm Para Anclaje Lateral",
             "categoria": "Sistema Bloques FX50 Anclaje Lateral"},
        ])

        resultados = self._buscar("bloque")
        nombres = [r["nombre"] for r in resultados]

        self.assertIn("Block PC clase A 20 x 20 x 40 cm", nombres)
        self.assertIn("Bloque De Aluminio 300 Mm Para Anclaje Lateral", nombres)

    def test_perlin_encuentra_perfil(self):
        self._cargar_y_reconstruir_indice([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Perfil C hierro negro 50 x 200 x 2,38 mm",
             "categoria": "Construccion"},
        ])

        resultados = self._buscar("perlin")

        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0]["nombre"], "Perfil C hierro negro 50 x 200 x 2,38 mm")

    def test_perlin_sin_sinonimo_habria_devuelto_vacio(self):
        # Confirma que el catálogo de prueba NO tiene la palabra "perlin"
        # literal -- si este assert fallara, el test anterior no probaría
        # nada real.
        self._cargar_y_reconstruir_indice([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Perfil C hierro negro 50 x 200 x 2,38 mm",
             "categoria": "Construccion"},
        ])
        conexion = sqlite3.connect(self.ruta_db)
        fila = conexion.execute(
            "SELECT COUNT(*) FROM productos WHERE nombre LIKE '%perlin%'"
        ).fetchone()
        conexion.close()
        self.assertEqual(fila[0], 0)


class PruebaReconstruirIndiceLiberaElCandadoSiFalla(BasePruebaBuscarFts):
    """Regresión de un bug real encontrado investigando "database is
    locked" en producción: si algo entre el INSERT ('delete-all') y el
    SELECT de reconstruir_indice() lanzaba una excepción, la conexión
    quedaba sin cerrar -- la transacción del 'delete-all' seguía abierta,
    con el candado de escritura pegado por el resto de la vida del
    proceso. Ahora un try/finally garantiza el close() siempre, haya
    pasado lo que haya pasado."""

    def test_conexion_queda_cerrada_y_sin_candado_aunque_falle_a_mitad_de_camino(self):
        conexion_setup = sqlite3.connect(self.ruta_db)
        _insertar(conexion_setup, proveedor="EPA", id_proveedor="1", nombre="Cemento")
        conexion_setup.commit()
        # Elimina `productos` a propósito -- el INSERT sobre productos_fts
        # ('delete-all') va a pasar, pero el SELECT FROM productos que
        # sigue va a fallar con "no such table: productos", exactamente
        # el escenario real que expuso el bug.
        conexion_setup.execute("DROP TABLE productos")
        conexion_setup.commit()
        conexion_setup.close()

        import db
        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            import busqueda
            with self.assertRaises(sqlite3.OperationalError):
                busqueda.reconstruir_indice()

        # Si la conexión de reconstruir_indice() hubiera quedado abierta
        # con una transacción sin confirmar, esta escritura -- sin
        # busy_timeout, igual que como corre en producción -- fallaría al
        # instante con "database is locked".
        conexion_de_prueba = sqlite3.connect(self.ruta_db)
        conexion_de_prueba.execute("CREATE TABLE prueba_sin_candado (id INTEGER)")
        conexion_de_prueba.commit()
        conexion_de_prueba.close()


if __name__ == "__main__":
    unittest.main()
