"""Pruebas para lectura_planos/ (ver LECTURA_DE_PLANOS_V1_MVP.md).

Dos grupos, a propósito separados:

1. Pruebas unitarias puras sobre las funciones de patrón/heurística
   (clasificación de disciplina, extracción de código+nombre desde líneas
   de texto) -- no abren ningún PDF, corren en cualquier máquina.

2. Pruebas de integración contra los DOS planos reales usados en la
   auditoría (ver LECTURA_DE_PLANOS_V1_MVP.md) -- estos PDFs NO están en
   el repositorio (son archivos grandes y privados del usuario, viven en
   su carpeta de Descargas). Se saltan automáticamente si no están
   presentes en la máquina donde corre la prueba, en vez de fallar --
   documentado así explícitamente porque es una desviación del resto del
   proyecto (que siempre prueba contra datos reales committeados, como
   database/proyecta.db). Confirmado su contenido real una sola vez
   durante la auditoría manual; estas pruebas fijan esos números como
   regresión.
"""

import os
import unittest

from lectura_planos.clasificacion import clasificar_disciplina
from lectura_planos.extractores import PATRON_CODIGO_LAMINA, _extraer_codigo_y_nombre
from lectura_planos.modelo import TipoPdf

RUTA_PLANO_ESTRUCTURAL = "/Users/joseandresbarzuna/Downloads/2022-12-13 Planos taller peralon Rev.pdf"
RUTA_PLANO_ARQUITECTONICO = "/Users/joseandresbarzuna/Downloads/20250312 - Planos Arquitectonicos.pdf"

PLANOS_REALES_DISPONIBLES = os.path.exists(RUTA_PLANO_ESTRUCTURAL) and os.path.exists(RUTA_PLANO_ARQUITECTONICO)


class PruebaPatronCodigoLamina(unittest.TestCase):
    def test_acepta_codigos_reales_observados(self):
        for codigo in ("A002", "A403", "A703", "S104", "S401", "A001"):
            self.assertTrue(PATRON_CODIGO_LAMINA.match(codigo), codigo)

    def test_rechaza_cedulas_y_folios(self):
        for texto in ("3-102-762158", "201529-F-000", "1-1876764-2016"):
            self.assertFalse(PATRON_CODIGO_LAMINA.match(texto), texto)

    def test_rechaza_referencias_de_detalle_con_un_solo_digito(self):
        # "VT1" no es un código de lámina -- son 2-4 dígitos, no 1.
        self.assertFalse(PATRON_CODIGO_LAMINA.match("VT1"))


class PruebaExtraccionCodigoYNombre(unittest.TestCase):
    def test_caso_real_plano_arquitectonico(self):
        lineas = [
            "27/2/2025 08:41:30",
            "VISTAS DE DESCANSO AL OESTE SRL",
            "3-102-890127",
            "A002",
            "1-1876764-2016",
            "PLANTA DE CONJUNTO N 0.0 M",
            "-",
        ]
        codigo, nombre = _extraer_codigo_y_nombre(lineas)
        self.assertEqual(codigo, "A002")
        self.assertEqual(nombre, "PLANTA DE CONJUNTO N 0.0 M")

    def test_caso_real_plano_estructural_sin_nombre_en_cajetin(self):
        # El plano de taller no expone un campo de nombre de lámina
        # separado -- el código es la última línea del cajetín.
        lineas = ["FECHA", "LÁMINA", "01/06/2022", "S104"]
        codigo, nombre = _extraer_codigo_y_nombre(lineas)
        self.assertEqual(codigo, "S104")
        self.assertIsNone(nombre)

    def test_descarta_ruido_de_una_sola_letra_como_nombre(self):
        # Caso real: una tabla de despiece pegada al margen del cajetín
        # dejó una "q" suelta después del código -- no debe tomarse como
        # nombre de lámina (ver LECTURA_DE_PLANOS_V1_MVP.md, limitaciones).
        lineas = ["01/06/2022", "S103", "q", "-", "I", "-"]
        codigo, nombre = _extraer_codigo_y_nombre(lineas)
        self.assertEqual(codigo, "S103")
        self.assertIsNone(nombre)

    def test_sin_codigo_no_hay_nombre(self):
        self.assertEqual(_extraer_codigo_y_nombre(["cualquier cosa", "sin código real"]), (None, None))

    def test_usa_el_ultimo_codigo_si_hay_varios(self):
        lineas = ["A001", "texto intermedio que no es un código", "A002", "NOMBRE REAL"]
        codigo, nombre = _extraer_codigo_y_nombre(lineas)
        self.assertEqual(codigo, "A002")
        self.assertEqual(nombre, "NOMBRE REAL")


class PruebaClasificarDisciplina(unittest.TestCase):
    def test_casos_reales_observados(self):
        casos = {
            "PLANTA DE CONJUNTO N 0.0 M": "sitio",
            "PLANTA DE DISTRIBUCION ARQUITECTONICA N 0.0 M": "arquitectonico",
            "PLANTA DE PUERTAS Y VENTANAS N +3.50 M": "puertas_ventanas",
            "PLANTA DE ACABADOS DE PAREDES Y PISOS N +3.50 M": "acabados",
            "PLANTA DE LUMINARIAS Y TOMACORRIENTES N 0.0 M": "electrico",
            "PLANTA ESTRUCTURAL DE CORONAS Y ENTREPISO": "estructural",
            "PLANTA ARQUITECTONICA DE CUBIERTAS N +6.50 M": "techos",
            "DETALLES ARQUITECTONICOS": "detalle",
            "PORTADA, INDICE Y LOCALIZACION": "indice",
        }
        for texto, esperado in casos.items():
            self.assertEqual(clasificar_disciplina(texto), esperado, texto)

    def test_texto_sin_palabra_clave_conocida_no_adivina(self):
        self.assertIsNone(clasificar_disciplina("XYZ 123 sin ninguna palabra reconocible"))


@unittest.skipUnless(
    PLANOS_REALES_DISPONIBLES,
    "Requiere los dos planos reales de la auditoría en ~/Downloads (no están en el repositorio, ver docstring).",
)
class PruebaIntegracionPlanosReales(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import lectura_planos as lp

        cls.lp = lp
        cls.estructural = lp.leer_proyecto(RUTA_PLANO_ESTRUCTURAL)
        cls.arquitectonico = lp.leer_proyecto(RUTA_PLANO_ARQUITECTONICO)

    def test_cuenta_todas_las_paginas_del_pdf(self):
        self.assertEqual(self.estructural.cantidad_laminas, 19)
        self.assertEqual(self.arquitectonico.cantidad_laminas, 58)

    def test_ambos_planos_son_100_por_ciento_vectorial_con_texto(self):
        # El único hallazgo de tipo de PDF con evidencia real -- ver
        # LECTURA_DE_PLANOS_V1_MVP.md, limitaciones.
        for lamina in self.estructural.laminas + self.arquitectonico.laminas:
            self.assertEqual(lamina.tipo_pdf, TipoPdf.VECTORIAL_CON_TEXTO)

    def test_indice_solo_se_encuentra_donde_existe(self):
        self.assertIsNone(self.estructural.indice)
        self.assertIsNotNone(self.arquitectonico.indice)
        self.assertGreater(len(self.arquitectonico.indice), 50)

    def test_nombre_del_proyecto_arquitectonico_viene_del_cajetin(self):
        self.assertEqual(self.arquitectonico.nombre, "RESIDENCIA S+Q")

    def test_nombre_del_proyecto_estructural_cae_al_nombre_de_archivo(self):
        # Este PDF no tiene el campo "PROYECTO:" lleno -- se documenta como
        # advertencia explícita, no un fallo silencioso.
        self.assertIn("Planos taller peralon", self.estructural.nombre)
        self.assertTrue(
            any("nombre del proyecto" in adv for adv in self.estructural.advertencias)
        )

    def test_codigos_de_lamina_conocidos_se_leen_correctamente(self):
        casos = {5: "S104", 15: "S114", 18: "S401"}
        for numero_pagina, codigo_esperado in casos.items():
            lamina = self.lp.lamina_por_pagina(self.estructural, numero_pagina)
            self.assertEqual(lamina.codigo, codigo_esperado)

        casos = {2: "A002", 29: "A403", 36: "A703"}
        for numero_pagina, codigo_esperado in casos.items():
            lamina = self.lp.lamina_por_pagina(self.arquitectonico, numero_pagina)
            self.assertEqual(lamina.codigo, codigo_esperado)

    def test_disciplina_del_proyecto_estructural_es_homogenea(self):
        # Todo el juego de "taller" es de detalle/estructura de madera.
        self.assertIn(self.estructural.disciplina, ("estructural", "detalle"))

    def test_disciplina_del_proyecto_arquitectonico_es_mixta(self):
        self.assertEqual(self.arquitectonico.disciplina, "mixto")

    def test_ninguna_lamina_del_arquitectonico_se_queda_sin_codigo(self):
        self.assertEqual(len(self.lp.laminas_sin_codigo(self.arquitectonico)), 0)

    def test_resumen_nunca_esconde_lo_que_no_se_pudo_determinar(self):
        resumen_estructural = self.lp.resumen(self.estructural)
        self.assertGreater(resumen_estructural["laminas_sin_nombre"], 0)
        self.assertGreater(resumen_estructural["laminas_por_disciplina"].get("sin_determinar", 0), 0)


if __name__ == "__main__":
    unittest.main()
