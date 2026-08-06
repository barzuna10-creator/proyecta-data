"""Pruebas para api/repositorio_proyectos.py::generar_cotizacion_automatica
-- la orquestación que corre seleccion_automatica.py sobre cada material
de un plano ya analizado y agrega, vía agregar_item() (sin duplicar su
lógica), los que el motor logró emparejar con un producto real.

Usa una base SQLite temporal con FTS5 real (catálogo real, chico y
controlado) -- mismo patrón que tests/test_seleccion_automatica.py, más
las tablas de proyectos/items ya usadas en tests/test_analizar_plano.py."""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from api.repositorio_proyectos import actualizar_item, crear_proyecto, generar_cotizacion_automatica


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
        "CREATE VIRTUAL TABLE productos_fts USING fts5(nombre, categoria, subcategoria, content='')"
    )
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
            confianza_match TEXT,
            revisado INTEGER NOT NULL DEFAULT 1,
            UNIQUE(proyecto_id, proveedor, id_proveedor)
        )
        """
    )
    conexion.commit()
    conexion.close()
    return archivo.name


def _insertar_producto(conexion, **campos):
    base = {
        "proveedor": "EPA", "id_proveedor": None, "sku": None, "nombre": None,
        "marca": None, "categoria": None, "subcategoria": None, "precio": 10000,
        "descripcion": None, "url_imagen": None, "url_producto": None,
        "peso": None, "imagenes_adicionales": None, "familia_id": None,
    }
    base.update(campos)
    columnas = ", ".join(base.keys())
    marcadores = ", ".join("?" for _ in base)
    conexion.execute(
        f"INSERT INTO productos ({columnas}) VALUES ({marcadores})", list(base.values())
    )


ANALISIS_CON_UNA_PUERTA_MATCHEABLE_Y_UNA_NO = {
    "proyecto_nombre": "Casa de prueba", "cantidad_laminas": 1,
    "niveles": [], "espacios": [],
    "puertas": [
        {
            "codigo": "P1", "ancho": None, "alto": None, "tipo": "corrediza",
            "material": "madera laurel", "cantidad": 2, "pagina_fuente": 5,
            "texto_original": "puerta corrediza madera laurel",
            "confianza": "alta", "termino_busqueda": "corrediza madera laurel",
        },
        {
            "codigo": "P2", "ancho": None, "alto": None, "tipo": "abatible",
            "material": "concepto inexistente xyz", "cantidad": 1, "pagina_fuente": 6,
            "texto_original": "puerta abatible concepto inexistente xyz",
            "confianza": "media", "termino_busqueda": "abatible concepto inexistente xyz",
        },
    ],
    "ventanas": [], "acabados": [], "piezas_estructurales": [],
    "laminas": {}, "advertencias": [],
}


class PruebaGenerarCotizacionAutomatica(unittest.TestCase):
    PROPIETARIO = "propietario-seleccion-automatica-test"

    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch_db = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch_db.start()

        conexion = sqlite3.connect(self.ruta_db)
        _insertar_producto(
            conexion, id_proveedor="1",
            nombre="Puerta corrediza madera laurel 90 x 210 cm",
            categoria="Maderas y puertas",
        )
        conexion.commit()
        conexion.close()

        import busqueda
        busqueda.reconstruir_indice()

        self.proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con plano")
        conexion = sqlite3.connect(self.ruta_db)
        conexion.execute(
            "UPDATE proyectos SET plano_analisis = ? WHERE id = ?",
            (json.dumps(ANALISIS_CON_UNA_PUERTA_MATCHEABLE_Y_UNA_NO), self.proyecto["id"]),
        )
        conexion.commit()
        conexion.close()

    def tearDown(self):
        self._patch_db.stop()
        os.remove(self.ruta_db)

    def test_agrega_un_item_para_el_material_que_si_matchea(self):
        resultado = generar_cotizacion_automatica(self.proyecto["id"], self.PROPIETARIO)
        nombres = [item["nombre"] for item in resultado["items"]]
        self.assertIn("Puerta corrediza madera laurel 90 x 210 cm", nombres)

    def test_no_agrega_nada_para_el_material_sin_match(self):
        resultado = generar_cotizacion_automatica(self.proyecto["id"], self.PROPIETARIO)
        # Solo el material P1 (matcheable) genera un ítem -- P2 (concepto
        # inexistente en el catálogo de prueba) no debe crear nada.
        self.assertEqual(len(resultado["items"]), 1)

    def test_el_resumen_cuenta_agregados_y_sin_seleccion(self):
        resultado = generar_cotizacion_automatica(self.proyecto["id"], self.PROPIETARIO)
        self.assertEqual(resultado["cotizacion_automatica_resumen"], {"agregados": 1, "sin_seleccion": 1})

    def test_el_item_agregado_nace_sin_revisar_con_trazabilidad_de_plano(self):
        resultado = generar_cotizacion_automatica(self.proyecto["id"], self.PROPIETARIO)
        item = resultado["items"][0]
        self.assertFalse(item["revisado"])
        self.assertEqual(item["origen"], "plano")
        self.assertEqual(item["pagina_fuente"], 5)
        self.assertEqual(item["regla_generadora"], "cuadro_puertas")
        self.assertIn(item["confianza_match"], ("alta", "media", "baja"))

    def test_la_cotizacion_ya_refleja_el_item_agregado(self):
        resultado = generar_cotizacion_automatica(self.proyecto["id"], self.PROPIETARIO)
        self.assertEqual(resultado["cotizacion"]["subtotal_materiales"], 10000 * 2)

    def test_cantidad_del_material_del_plano_se_respeta(self):
        resultado = generar_cotizacion_automatica(self.proyecto["id"], self.PROPIETARIO)
        self.assertEqual(resultado["items"][0]["cantidad"], 2)

    def test_marcar_revisado_manualmente_lo_saca_de_pendiente(self):
        resultado = generar_cotizacion_automatica(self.proyecto["id"], self.PROPIETARIO)
        item_id = resultado["items"][0]["id"]

        actualizado = actualizar_item(self.proyecto["id"], self.PROPIETARIO, item_id, {"revisado": True})
        item = next(i for i in actualizado["items"] if i["id"] == item_id)
        self.assertTrue(item["revisado"])

    def test_proyecto_inexistente_devuelve_none(self):
        self.assertIsNone(generar_cotizacion_automatica(999999, self.PROPIETARIO))

    def test_no_deja_generar_para_el_plano_de_otro_propietario(self):
        self.assertIsNone(
            generar_cotizacion_automatica(self.proyecto["id"], "otro-propietario-distinto")
        )

    def test_proyecto_sin_plano_analizado_lanza_value_error(self):
        otro = crear_proyecto(self.PROPIETARIO, "Proyecto sin plano")
        with self.assertRaises(ValueError):
            generar_cotizacion_automatica(otro["id"], self.PROPIETARIO)

    def test_correrlo_dos_veces_no_duplica_el_item(self):
        generar_cotizacion_automatica(self.proyecto["id"], self.PROPIETARIO)
        resultado = generar_cotizacion_automatica(self.proyecto["id"], self.PROPIETARIO)
        self.assertEqual(len(resultado["items"]), 1)


if __name__ == "__main__":
    unittest.main()
