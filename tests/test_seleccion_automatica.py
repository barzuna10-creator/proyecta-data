"""Pruebas para seleccion_automatica.py -- el motor que elige un producto
real del catálogo para un material detectado en un plano (puerta, ventana,
acabado, pieza estructural).

Foco explícito de estas pruebas (pedido del usuario: "no quiero
coincidencias simples solo por palabras si pueden generar materiales
incorrectos"): que los tres vetos duros (repuesto/accesorio, "no empieza
por el concepto buscado", conflicto de medida) de verdad descarten
candidatos, no solo que existan en el código. Cada prueba de veto arma un
catálogo donde, SIN el veto correspondiente, el candidato malo ganaría por
puntaje de texto -- así una prueba que pase con el veto roto/removido
falla de verdad, no por casualidad.

Usa unittest + FTS5 real sobre una base SQLite temporal (mismo patrón que
tests/test_busqueda.py) -- nunca toca database/proyecta.db."""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from seleccion_automatica import (
    medida_pieza_estructural_cm,
    medida_puerta_ventana_cm,
    seleccionar_para_acabado,
    seleccionar_para_pieza_estructural,
    seleccionar_para_puerta_ventana,
    seleccionar_producto,
)


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
    # buscar_fts() hace LEFT JOIN familias_producto -- sin esta tabla, esa
    # consulta lanza "no such table" y buscar_fts() lo atrapa en su except
    # sqlite3.OperationalError (pensado para errores de sintaxis FTS5, no
    # para esto) y devuelve [] en silencio. Encontrado de verdad escribiendo
    # esta prueba: sin esta tabla, TODAS las búsquedas positivas fallaban.
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


def _insertar(conexion, **campos):
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


class BasePruebaSeleccion(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal()

    def tearDown(self):
        os.remove(self.ruta_db)

    def _cargar_catalogo(self, filas):
        conexion = sqlite3.connect(self.ruta_db)
        for indice, fila in enumerate(filas):
            fila.setdefault("id_proveedor", str(indice + 1))
            _insertar(conexion, **fila)
        conexion.commit()
        conexion.close()

        import db
        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            import busqueda
            busqueda.reconstruir_indice()

    def _seleccionar(self, *args, **kwargs):
        import db
        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            return seleccionar_producto(*args, **kwargs)


class PruebaVetoRepuestoAccesorio(BasePruebaSeleccion):
    def test_no_selecciona_un_repuesto_aunque_sea_el_mejor_texto(self):
        self._cargar_catalogo([
            {"nombre": "Repuesto bisagra para puerta corrediza madera", "categoria": "Maderas y puertas"},
        ])
        resultado = self._seleccionar("puerta corrediza madera")
        self.assertIsNone(resultado["producto"])
        self.assertIsNone(resultado["confianza"])

    def test_si_hay_una_alternativa_real_la_elige_en_vez_del_repuesto(self):
        self._cargar_catalogo([
            {"nombre": "Repuesto bisagra para puerta corrediza madera", "categoria": "Maderas y puertas"},
            {"nombre": "Puerta corrediza madera laurel 90 x 210 cm", "categoria": "Maderas y puertas"},
        ])
        resultado = self._seleccionar("puerta corrediza madera")
        self.assertIsNotNone(resultado["producto"])
        self.assertIn("Puerta corrediza madera laurel", resultado["producto"]["nombre"])


class PruebaVetoNoEmpiezaPorElConcepto(BasePruebaSeleccion):
    """Caso real encontrado calibrando este módulo contra el catálogo real:
    buscar "Enchape de Porcelanato" devolvía primero "Sistema para
    manipular porcelanatos" -- una herramienta de instalación, no el
    porcelanato en sí. FTS5 indexa categoría además del nombre, así que el
    AND de tokens por sí solo no bastaba."""

    def test_no_selecciona_una_herramienta_de_instalacion_del_material(self):
        self._cargar_catalogo([
            {
                "nombre": "Sistema para manipular porcelanatos de formato grande",
                "categoria": "Pisos y Enchapes", "subcategoria": "Herramientas",
            },
        ])
        resultado = self._seleccionar("enchape porcelanato")
        self.assertIsNone(resultado["producto"])

    def test_si_hay_un_producto_real_lo_prefiere_sobre_la_herramienta(self):
        self._cargar_catalogo([
            {
                "nombre": "Sistema para manipular porcelanatos de formato grande",
                "categoria": "Pisos y Enchapes", "subcategoria": "Herramientas",
            },
            {"nombre": "Porcelanato gris 60 x 60 cm", "categoria": "Pisos y Enchapes"},
        ])
        resultado = self._seleccionar("enchape porcelanato")
        self.assertIsNotNone(resultado["producto"])
        self.assertIn("Porcelanato gris", resultado["producto"]["nombre"])

    def test_singular_y_plural_del_mismo_concepto_se_reconocen(self):
        # "porcelanato" (búsqueda) vs "porcelanatos" (candidato) -- no
        # deben tratarse como conceptos distintos solo por el plural.
        self._cargar_catalogo([
            {"nombre": "Porcelanatos importados serie Milano 60 x 60 cm", "categoria": "Pisos y Enchapes"},
        ])
        resultado = self._seleccionar("porcelanato")
        self.assertIsNotNone(resultado["producto"])


class PruebaVetoConflictoDeMedida(BasePruebaSeleccion):
    def test_candidato_con_medida_explicitamente_distinta_se_descarta(self):
        self._cargar_catalogo([
            {"nombre": "Puerta closet laurel 60 x 200 cm", "categoria": "Maderas y puertas"},
        ])
        # Plano pide una puerta de 0.95 x 2.40 m (95 x 240 cm) -- muy
        # distinta de la de 60 x 200 cm que sí existe en el catálogo.
        resultado = self._seleccionar("puerta laurel", medida_esperada_cm=(95, 240))
        self.assertIsNone(resultado["producto"])

    def test_candidato_con_medida_que_calza_dentro_de_tolerancia_se_confirma(self):
        self._cargar_catalogo([
            {"nombre": "Puerta closet laurel 60 x 200 cm", "categoria": "Maderas y puertas"},
        ])
        resultado = self._seleccionar("puerta laurel", medida_esperada_cm=(60, 200))
        self.assertIsNotNone(resultado["producto"])
        self.assertEqual(resultado["confianza"], "alta")

    def test_medida_invertida_ancho_alto_tambien_se_reconoce(self):
        self._cargar_catalogo([
            {"nombre": "Puerta closet laurel 200 x 60 cm", "categoria": "Maderas y puertas"},
        ])
        resultado = self._seleccionar("puerta laurel", medida_esperada_cm=(60, 200))
        self.assertIsNotNone(resultado["producto"])
        self.assertEqual(resultado["confianza"], "alta")

    def test_candidato_sin_medida_en_el_nombre_no_se_descarta_por_medida(self):
        self._cargar_catalogo([
            {"nombre": "Puerta laurel para closet", "categoria": "Maderas y puertas"},
        ])
        resultado = self._seleccionar("puerta laurel", medida_esperada_cm=(95, 240))
        self.assertIsNotNone(resultado["producto"])
        # Sin corroboración (ninguno de los dos lados confirma), nunca
        # confianza alta solo por texto.
        self.assertNotEqual(resultado["confianza"], "alta")


class PruebaNivelesDeConfianza(BasePruebaSeleccion):
    def test_sin_candidatos_de_busqueda_devuelve_sin_match(self):
        self._cargar_catalogo([{"nombre": "Cemento gris 50 kg", "categoria": "Cemento"}])
        resultado = self._seleccionar("bombilla led")
        self.assertIsNone(resultado["producto"])
        self.assertIsNone(resultado["confianza"])

    def test_categoria_esperada_confirmada_sube_a_confianza_alta_en_primer_lugar(self):
        self._cargar_catalogo([
            {"nombre": "Puerta laurel para closet", "categoria": "Maderas y puertas"},
        ])
        sin_categoria = self._seleccionar("puerta laurel")
        con_categoria = self._seleccionar("puerta laurel", categoria_esperada={"puerta"})
        self.assertEqual(sin_categoria["confianza"], "media")
        self.assertEqual(con_categoria["confianza"], "alta")

    def test_sin_precio_no_impide_la_seleccion_pero_no_es_ideal(self):
        self._cargar_catalogo([
            {"nombre": "Puerta laurel para closet", "categoria": "Maderas y puertas", "precio": None},
        ])
        resultado = self._seleccionar("puerta laurel", depurar=True)
        self.assertIsNotNone(resultado["producto"])
        self.assertIn("sin_precio", resultado["razones"])


class PruebaConversionesDeMedida(unittest.TestCase):
    def test_medida_puerta_ventana_convierte_metros_a_cm(self):
        material = {"ancho": 0.95, "alto": 2.40}
        self.assertEqual(medida_puerta_ventana_cm(material), (95.0, 240.0))

    def test_medida_puerta_ventana_sin_dato_devuelve_none(self):
        self.assertIsNone(medida_puerta_ventana_cm({"ancho": None, "alto": 2.40}))
        self.assertIsNone(medida_puerta_ventana_cm({}))

    def test_medida_pieza_convierte_mm_a_cm(self):
        material = {"ancho_mm": 90.0, "alto_mm": 245.0}
        self.assertEqual(medida_pieza_estructural_cm(material), (9.0, 24.5))

    def test_medida_pieza_sin_dato_devuelve_none(self):
        self.assertIsNone(medida_pieza_estructural_cm({"ancho_mm": None, "alto_mm": None}))


class PruebaSeleccionarParaTipoDeMaterial(BasePruebaSeleccion):
    """seleccionar_para_puerta_ventana/acabado/pieza_estructural --
    los adaptadores que reciben el dict tal como viene de
    api/adaptador_planos.py."""

    def test_puerta_antepone_la_palabra_puerta_a_la_busqueda(self):
        # termino_busqueda real de una puerta NUNCA incluye la palabra
        # "puerta" (ver api/adaptador_planos.py -- solo tipo+material) --
        # sin anteponerla, esta búsqueda no encontraría nada.
        self._cargar_catalogo([
            {"nombre": "Puerta corrediza madera laurel 90 x 210 cm", "categoria": "Maderas y puertas"},
        ])
        material = {
            "ancho": None, "alto": None, "termino_busqueda": "corrediza madera",
            "pagina_fuente": 1, "texto_original": "x", "confianza": "alta",
        }
        import db
        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            resultado = seleccionar_para_puerta_ventana(material, es_ventana=False)
        self.assertIsNotNone(resultado["producto"])

    def test_ventana_antepone_ventana_no_puerta(self):
        self._cargar_catalogo([
            {"nombre": "Puerta corrediza vidrio templado 90 x 210 cm", "categoria": "Maderas y puertas"},
            {"nombre": "Ventana corrediza vidrio templado aluminio", "categoria": "Ventaneria"},
        ])
        material = {
            "ancho": None, "alto": None, "termino_busqueda": "corrediza vidrio",
            "pagina_fuente": 1, "texto_original": "x", "confianza": "alta",
        }
        import db
        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            resultado = seleccionar_para_puerta_ventana(material, es_ventana=True)
        self.assertIsNotNone(resultado["producto"])
        self.assertIn("Ventana", resultado["producto"]["nombre"])

    def test_acabado_usa_ubicacion_como_categoria_esperada(self):
        self._cargar_catalogo([
            {"nombre": "Porcelanato gris 60 x 60 cm", "categoria": "Pisos y Enchapes"},
        ])
        material = {
            "termino_busqueda": "porcelanato", "ubicacion": "pisos",
            "pagina_fuente": 1, "texto_original": "x", "confianza": "media",
        }
        import db
        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            resultado = seleccionar_para_acabado(material)
        self.assertIsNotNone(resultado["producto"])

    def test_pieza_estructural_usa_ancho_mm_alto_mm(self):
        self._cargar_catalogo([
            {"nombre": "Viga de madera 90 x 245 mm tratada", "categoria": "Maderas y puertas"},
        ])
        material = {
            "termino_busqueda": "viga madera", "ancho_mm": 90.0, "alto_mm": 245.0, "largo_m": 6.0,
            "pagina_fuente": 1, "texto_original": "x", "confianza": "alta",
        }
        import db
        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            resultado = seleccionar_para_pieza_estructural(material)
        self.assertIsNotNone(resultado["producto"])
        self.assertEqual(resultado["confianza"], "alta")


class PruebaAncla(BasePruebaSeleccion):
    """ancla (agregado tras medir cobertura contra el catálogo real): sin
    ella, el veto de concepto acepta un candidato que empiece por
    CUALQUIER token de la búsqueda, incluido el material en vez del
    objeto. Caso real: "puerta lamina de hn" no encuentra ninguna puerta,
    se relaja a "puerta lamina", y sin ancla una lámina plana (no una
    puerta) pasa porque su categoría "Maderas y puertas" hace que el
    token "puerta" también calce ahí -- FTS5 indexa categoría además del
    nombre (ver el docstring del módulo)."""

    def test_sin_ancla_un_candidato_que_solo_comparte_categoria_pasa(self):
        self._cargar_catalogo([
            {"nombre": "Lamina melamina MDP 4 pies 16 mm negra", "categoria": "Maderas y puertas"},
        ])
        resultado = self._seleccionar("puerta lamina de hn")
        self.assertIsNotNone(resultado["producto"])  # documenta el riesgo real sin ancla

    def test_con_ancla_el_mismo_candidato_se_veta(self):
        self._cargar_catalogo([
            {"nombre": "Lamina melamina MDP 4 pies 16 mm negra", "categoria": "Maderas y puertas"},
        ])
        resultado = self._seleccionar("puerta lamina de hn", ancla="puerta")
        self.assertIsNone(resultado["producto"])

    def test_con_ancla_una_puerta_real_si_pasa(self):
        self._cargar_catalogo([
            {"nombre": "Puerta lamina de hn con acabado pintura", "categoria": "Maderas y puertas"},
        ])
        resultado = self._seleccionar("puerta lamina de hn", ancla="puerta")
        self.assertIsNotNone(resultado["producto"])


class PruebaRelajacionProgresiva(BasePruebaSeleccion):
    """_buscar_con_relajacion: si la consulta completa no encuentra nada,
    recorta tokens desde el final -- pero nunca por debajo de
    MINIMO_TOKENS_RELAJACION=2. Caso real: relajar "national cielo gypsum
    mr" hasta la sola palabra "national" encontraba una bisagra de esa
    marca -- coincidencia de marca, no de tipo de producto."""

    def test_relaja_la_consulta_hasta_dos_tokens_y_encuentra_el_producto(self):
        self._cargar_catalogo([
            {"nombre": "Repello Fino gris REPEMAX 90 40Kg", "categoria": "Construccion"},
        ])
        # La descripción completa del plano casi nunca calza literal.
        resultado = self._seleccionar(
            "REPELLO FINO ENMASILLADO CON PINTURA COLOR BLANCO", depurar=True
        )
        self.assertIsNotNone(resultado["producto"])
        self.assertIn("consulta_relajada", resultado["razones"])

    def test_nunca_relaja_hasta_un_solo_token(self):
        self._cargar_catalogo([
            {"nombre": "National Bisagra 3x3 CTE dorada", "categoria": "Bisagras"},
        ])
        # "national cielo gypsum mr" no debe relajarse hasta la sola
        # palabra "national" -- coincidencia de marca, no de producto.
        resultado = self._seleccionar("national cielo gypsum mr")
        self.assertIsNone(resultado["producto"])


class PruebaCategoriaProhibida(BasePruebaSeleccion):
    """categoria_prohibida: veto duro por dominio inequívocamente ajeno --
    reservado para casos donde NINGÚN candidato de esa categoría puede ser
    el producto real buscado. Caso real: "columna" (pieza estructural de
    madera/acero) encuentra, en el catálogo real, únicamente columnas de
    ducha, topes de estacionamiento y repuestos de góndola -- ninguna
    columna estructural."""

    def test_veta_un_candidato_de_la_categoria_prohibida(self):
        self._cargar_catalogo([
            {"nombre": "Columna de ducha acero inoxidable negro mate", "categoria": "Baños"},
        ])
        resultado = self._seleccionar("columna", categoria_prohibida={"banos"})
        self.assertIsNone(resultado["producto"])

    def test_sin_categoria_prohibida_el_mismo_candidato_pasa(self):
        self._cargar_catalogo([
            {"nombre": "Columna de ducha acero inoxidable negro mate", "categoria": "Baños"},
        ])
        resultado = self._seleccionar("columna")
        self.assertIsNotNone(resultado["producto"])

    def test_una_columna_de_categoria_permitida_no_se_veta(self):
        self._cargar_catalogo([
            {"nombre": "Columna de madera tratada 20 x 20 cm", "categoria": "Maderas"},
        ])
        resultado = self._seleccionar("columna", categoria_prohibida={"banos"})
        self.assertIsNotNone(resultado["producto"])


class PruebaVetoDeNegacion(BasePruebaSeleccion):
    """Caso real: la fila "Sin cielo" de un cuadro de acabados (significa
    que ESE ambiente no lleva cielo raso) buscaba "cielo" y encontraba,
    con confianza alta, un cielo suspendido real -- exactamente lo
    opuesto de lo que dice el plano."""

    def test_un_termino_que_empieza_en_negacion_nunca_matchea(self):
        self._cargar_catalogo([
            {"nombre": "Cielo suspendido radar 2 x 2 x 5/8 pulg sin bisel", "categoria": "Cielos"},
        ])
        resultado = self._seleccionar("Sin cielo", depurar=True)
        self.assertIsNone(resultado["producto"])
        self.assertEqual(resultado["razones"], ["termino_de_negacion"])

    def test_un_termino_normal_con_sin_en_medio_si_matchea(self):
        self._cargar_catalogo([
            {"nombre": "Cielo suspendido radar 2 x 2 x 5/8 pulg sin bisel", "categoria": "Cielos"},
        ])
        resultado = self._seleccionar("cielo suspendido")
        self.assertIsNotNone(resultado["producto"])


class PruebaMedidaEnPulgadas(unittest.TestCase):
    """_medidas_en_nombre: "8" X 8"" (con comilla pegada a AMBOS números,
    no solo al segundo) no lo reconocía el patrón cm/mm/m -- un candidato
    así quedaba sin ninguna medida detectada y el veto de conflicto no
    tenía nada que comparar. Caso real: "Viga Estructural En Forma De H
    8\" X 8\". ... Alto 203.2 Mm"."""

    def test_reconoce_medida_en_pulgadas_y_la_convierte_a_cm(self):
        from seleccion_automatica import _medidas_en_nombre

        nombre = 'Viga Estructural En Forma De H 8" X 8". Alto 203.2 Mm'
        medidas = _medidas_en_nombre(nombre)
        self.assertIn((20.32, 20.32), medidas)


if __name__ == "__main__":
    unittest.main()
