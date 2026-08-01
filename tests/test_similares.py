"""Pruebas para similares.py: exclusión del producto actual, prioridad de
familia, rechazo de candidatos incompatibles, resultados de proveedores
distintos, y ausencia de recomendaciones cuando no hay señal confiable.

Usa unittest + una base SQLite temporal (mismo patrón que
tests/test_enriquecimiento.py) -- nunca toca database/proyecta.db.
"""

import sqlite3
import tempfile
import os
import unittest
from unittest import mock


def _crear_db_temporal():
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    conexion = sqlite3.connect(archivo.name)
    conexion.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor TEXT,
            id_proveedor TEXT,
            sku TEXT,
            nombre TEXT,
            marca TEXT,
            categoria TEXT,
            subcategoria TEXT,
            precio REAL,
            descripcion TEXT,
            url_imagen TEXT,
            url_producto TEXT,
            peso TEXT,
            imagenes_adicionales TEXT,
            familia_id INTEGER,
            UNIQUE(proveedor, id_proveedor)
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE familias_producto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor TEXT,
            categoria TEXT,
            firma_base TEXT,
            nombre_familia TEXT,
            fecha_calculo TEXT
        )
        """
    )
    conexion.commit()
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


class BasePruebaSimilares(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal()

    def tearDown(self):
        os.remove(self.ruta_db)

    def _cargar(self, filas):
        conexion = sqlite3.connect(self.ruta_db)
        for fila in filas:
            _insertar(conexion, **fila)
        conexion.commit()
        conexion.close()

    def _obtener_similares(self, proveedor, id_proveedor, **kwargs):
        import db
        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            import similares
            return similares.obtener_similares(proveedor, id_proveedor, **kwargs)


class PruebaExclusionProductoActual(BasePruebaSimilares):
    def test_no_se_recomienda_a_si_mismo(self):
        self._cargar([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Taladro percutor 750 W Daewoo",
             "categoria": "Herramientas", "subcategoria": "Taladros"},
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Taladro percutor 600 W Daewoo",
             "categoria": "Herramientas", "subcategoria": "Taladros"},
        ])

        resultados = self._obtener_similares("EPA", "1")
        ids = [(r["proveedor"], r["id_proveedor"]) for r in resultados]

        self.assertNotIn(("EPA", "1"), ids)
        self.assertIn(("EPA", "2"), ids)


class PruebaPrioridadFamilia(BasePruebaSimilares):
    def test_familia_gana_aunque_el_texto_se_parezca_menos(self):
        self._cargar([
            {
                "proveedor": "El Lagar", "id_proveedor": "1",
                "nombre": "Pintura Seal Block Blanco Galon Lanco",
                "categoria": "Pinturas", "subcategoria": "Interiores",
                "familia_id": 55,
            },
            # Misma familia, nombre bastante distinto en superficie (otra
            # presentación) -- debe ganarle al candidato de solo texto.
            {
                "proveedor": "El Lagar", "id_proveedor": "2",
                "nombre": "Pintura Seal Block Blanco Cuarto Lanco",
                "categoria": "Pinturas", "subcategoria": "Interiores",
                "familia_id": 55,
            },
            # Sin familia, pero comparte muchísimos tokens y subcategoría --
            # score alto por texto, pero debe quedar detrás del de familia.
            {
                "proveedor": "El Lagar", "id_proveedor": "3",
                "nombre": "Pintura Seal Block Blanco Galon Sur",
                "categoria": "Pinturas", "subcategoria": "Interiores",
                "familia_id": None,
            },
        ])

        resultados = self._obtener_similares("El Lagar", "1", depurar=True)

        self.assertGreaterEqual(len(resultados), 2)
        self.assertEqual(resultados[0]["id_proveedor"], "2")
        self.assertIn("misma_familia", resultados[0]["_razones"])
        self.assertGreater(resultados[0]["_puntaje"], resultados[1]["_puntaje"])


class PruebaRechazoIncompatibles(BasePruebaSimilares):
    def test_misma_categoria_sola_no_alcanza(self):
        self._cargar([
            {"proveedor": "Carbone Store", "id_proveedor": "1", "nombre": "Taladro Inalámbrico 20V",
             "categoria": "Herramientas", "subcategoria": "Taladros"},
            # Misma categoría, pero ni subcategoría ni tokens de nombre en
            # común -- no debe calificar como "similar".
            {"proveedor": "Carbone Store", "id_proveedor": "2", "nombre": "Martillo De Goma 450G",
             "categoria": "Herramientas", "subcategoria": "Martillos"},
        ])

        resultados = self._obtener_similares("Carbone Store", "1")
        ids = [r["id_proveedor"] for r in resultados]

        self.assertNotIn("2", ids)

    def test_categoria_general_exige_mas_tokens(self):
        self._cargar([
            {"proveedor": "Carbone Store", "id_proveedor": "1", "nombre": "Set Copas Vaso 12 Piezas",
             "categoria": "General", "subcategoria": None},
            # Comparte un solo token real ("piezas" -- descartado por ser
            # genérico no, pero probemos con un token real compartido único).
            {"proveedor": "Carbone Store", "id_proveedor": "2", "nombre": "Piezas De Repuesto Motosierra",
             "categoria": "General", "subcategoria": None},
        ])

        resultados = self._obtener_similares("Carbone Store", "1")
        ids = [r["id_proveedor"] for r in resultados]

        # Solo 1 token compartido real ("piezas") en categoría "General" --
        # se exige un mínimo de 2, así que no debe calificar.
        self.assertNotIn("2", ids)


class PruebaProveedoresDistintos(BasePruebaSimilares):
    def test_incluye_otro_proveedor_cuando_es_comparable(self):
        self._cargar([
            {
                "proveedor": "Carbone Store", "id_proveedor": "1",
                "nombre": "Taladro Inalámbrico HOTECHE 20V",
                "categoria": "Herramientas", "subcategoria": "Taladros Inalámbricos",
                "marca": "HOTECHE",
            },
            # Mismo proveedor, poca relación -- para confirmar que no gana
            # solo por ser del mismo proveedor.
            {
                "proveedor": "Carbone Store", "id_proveedor": "2",
                "nombre": "Escalera de Aluminio",
                "categoria": "Escaleras", "subcategoria": None,
            },
            # Proveedor distinto, subcategoría igual + tokens compartidos --
            # debe calificar igual que si fuera del mismo proveedor.
            {
                "proveedor": "El Lagar", "id_proveedor": "9",
                "nombre": "Taladro Inalámbrico 20V Bosch",
                "categoria": "Herramientas", "subcategoria": "Taladros Inalámbricos",
                "marca": "BOSCH",
            },
        ])

        resultados = self._obtener_similares("Carbone Store", "1")
        proveedores = {r["proveedor"] for r in resultados}

        self.assertIn("El Lagar", proveedores)


class PruebaSinRecomendacionesDebiles(BasePruebaSimilares):
    def test_lista_vacia_si_nada_califica(self):
        self._cargar([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cinta métrica 5 m carcasa nylon",
             "categoria": "Herramientas", "subcategoria": "Medición"},
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Martillo de goma 450 g",
             "categoria": "Herramientas", "subcategoria": "Martillos"},
            {"proveedor": "EPA", "id_proveedor": "3", "nombre": "Llave francesa ajustable",
             "categoria": "Herramientas", "subcategoria": "Llaves"},
        ])

        resultados = self._obtener_similares("EPA", "1")

        self.assertEqual(resultados, [])

    def test_nunca_devuelve_mas_del_limite_pedido(self):
        filas = [
            {"proveedor": "EPA", "id_proveedor": "0", "nombre": "Tornillo gypsum #6 1000u",
             "categoria": "Ferreteria", "subcategoria": "Tornillos"},
        ]
        for i in range(1, 10):
            filas.append({
                "proveedor": "EPA", "id_proveedor": str(i),
                "nombre": f"Tornillo gypsum #6 {i}00u",
                "categoria": "Ferreteria", "subcategoria": "Tornillos",
            })
        self._cargar(filas)

        resultados = self._obtener_similares("EPA", "0", limite=6)

        self.assertLessEqual(len(resultados), 6)


if __name__ == "__main__":
    unittest.main()
