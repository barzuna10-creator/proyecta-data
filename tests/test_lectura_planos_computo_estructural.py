"""Pruebas para lectura_planos/computo_estructural.py
(EXTRACTOR_COMPUTO_ESTRUCTURAL_V1 -- ver
EXTRACTOR_COMPUTO_ESTRUCTURAL_V1.md para la auditoría completa).

Mismo criterio que las fases anteriores: unitarias puras (patrón de
línea, deduplicación) + integración contra los dos planos reales, que se
salta si los archivos no están presentes."""

import os
import unittest

from lectura_planos.computo_estructural import PATRON_PIEZA, agregar_computo_estructural
from lectura_planos.modelo import Lamina, PiezaEstructural, Proyecto, TipoPdf

RUTA_PLANO_ESTRUCTURAL = "/Users/joseandresbarzuna/Downloads/2022-12-13 Planos taller peralon Rev.pdf"
RUTA_PLANO_ARQUITECTONICO = "/Users/joseandresbarzuna/Downloads/20250312 - Planos Arquitectonicos.pdf"
PLANOS_REALES_DISPONIBLES = os.path.exists(RUTA_PLANO_ESTRUCTURAL) and os.path.exists(RUTA_PLANO_ARQUITECTONICO)


def _lamina(numero_pagina):
    return Lamina(
        numero_pagina=numero_pagina, tipo_pdf=TipoPdf.VECTORIAL_CON_TEXTO,
        codigo=None, nombre=None, disciplina=None,
    )


def _pieza(cantidad, texto_original, pagina=4):
    return PiezaEstructural(
        cantidad=cantidad, ancho_mm=72, alto_mm=195, largo_m=4.4,
        descripcion="x", pagina_fuente=pagina, texto_original=texto_original, confianza="alta",
    )


class PruebaPatronPieza(unittest.TestCase):
    def test_caso_real_simple(self):
        m = PATRON_PIEZA.match("2 Piezas de 90mm x 245mm x 6.72mts Vigas de pergola")
        self.assertEqual(m.groups(), ("2", "90", "245", "6.72", "Vigas de pergola"))

    def test_singular_pieza(self):
        m = PATRON_PIEZA.match("1 Pieza de 98mm x 245mm x 3.40mts Ajuste para Viga pergola")
        self.assertEqual(m.group(1), "1")

    def test_caso_real_sin_la_segunda_x(self):
        # inconsistencia real encontrada en el propio cuadro: falta la "x"
        # antes de la tercera dimensión en una de las 11 líneas reales.
        m = PATRON_PIEZA.match("3 Piezas de 145mm x 245mm 5.82mts vigas áreas dormitorio principal")
        self.assertIsNotNone(m)
        self.assertEqual(m.groups()[:4], ("3", "145", "245", "5.82"))

    def test_pieza_en_minuscula(self):
        m = PATRON_PIEZA.match("24 piezas de 72mm x 195 mm x 4.40mts artesones dobles de sala o pergola")
        self.assertIsNotNone(m)

    def test_texto_libre_no_calza(self):
        self.assertIsNone(PATRON_PIEZA.match("PIEZA DE MADERA"))  # etiqueta de detalle de conexión, no cómputo
        self.assertIsNone(PATRON_PIEZA.match("Viga de concreto"))


class PruebaAgregarComputoEstructural(unittest.TestCase):
    def test_deduplica_por_hoja_completa_no_por_linea(self):
        # Caso real: la misma hoja trae dos filas con texto IDÉNTICO
        # ("columna" x2, dos piezas físicas distintas) -- deduplicar por
        # línea las colapsaba en una sola (bug real, corregido). Acá se
        # simulan 2 hojas con el mismo cuadro repetido, cada una con dos
        # líneas idénticas entre sí.
        piezas_hoja = [
            _pieza(1, "1 Pieza de 195mm x 195mm x 2.65mts columna"),
            _pieza(1, "1 Pieza de 195mm x 195mm x 2.65mts columna"),
        ]
        lamina_1 = _lamina(4)
        lamina_1.extras["computo_estructural"] = _ResultadoFalso(piezas_hoja)
        lamina_2 = _lamina(5)
        lamina_2.extras["computo_estructural"] = _ResultadoFalso(piezas_hoja)

        proyecto = Proyecto(
            ruta_pdf="x.pdf", nombre="x", cantidad_laminas=2, disciplina="estructural",
            laminas=[lamina_1, lamina_2], indice=None,
        )
        resultado = agregar_computo_estructural(proyecto)
        self.assertEqual(len(resultado["piezas"]), 2)  # las dos columnas, no colapsadas a 1
        self.assertEqual(resultado["cantidad_total_piezas"], 2)

    def test_paginas_sin_cuadro_no_producen_nada(self):
        lamina = _lamina(1)
        proyecto = Proyecto(
            ruta_pdf="x.pdf", nombre="x", cantidad_laminas=1, disciplina="sin_determinar",
            laminas=[lamina], indice=None,
        )
        resultado = agregar_computo_estructural(proyecto)
        self.assertEqual(resultado["piezas"], [])
        self.assertEqual(resultado["cantidad_total_piezas"], 0)
        # PRODUCTION_READINESS_REVIEW.md, hallazgo D1: 0 piezas nunca debe
        # verse igual que "todo salió bien" -- siempre trae una advertencia
        # explícita, para que no se confunda con un plano que genuinamente
        # no tiene cómputo estructural.
        self.assertEqual(len(resultado["advertencias"]), 1)
        self.assertIn("no se encontraron piezas", resultado["advertencias"][0])

    def test_hojas_con_contenido_distinto_se_senalan_no_se_fusionan(self):
        lamina_1 = _lamina(4)
        lamina_1.extras["computo_estructural"] = _ResultadoFalso([_pieza(2, "2 Piezas de 90mm x 245mm x 6.72mts vigas")])
        lamina_2 = _lamina(5)
        lamina_2.extras["computo_estructural"] = _ResultadoFalso([_pieza(3, "3 Piezas de 90mm x 245mm x 6.72mts vigas")])

        proyecto = Proyecto(
            ruta_pdf="x.pdf", nombre="x", cantidad_laminas=2, disciplina="estructural",
            laminas=[lamina_1, lamina_2], indice=None,
        )
        resultado = agregar_computo_estructural(proyecto)
        self.assertEqual(len(resultado["advertencias"]), 1)
        self.assertIn("2 versiones distintas", resultado["advertencias"][0])


class _ResultadoFalso:
    def __init__(self, filas):
        self.datos = {"filas": filas}
        self.advertencias = []


@unittest.skipUnless(
    PLANOS_REALES_DISPONIBLES,
    "Requiere los dos planos reales de la auditoría en ~/Downloads (no están en el repositorio).",
)
class PruebaIntegracionComputoEstructuralReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import lectura_planos as lp

        cls.estructural = lp.agregar_computo_estructural(lp.leer_proyecto(RUTA_PLANO_ESTRUCTURAL))
        cls.arquitectonico = lp.agregar_computo_estructural(lp.leer_proyecto(RUTA_PLANO_ARQUITECTONICO))

    def test_plano_estructural_da_las_11_piezas_reales(self):
        self.assertEqual(len(self.estructural["piezas"]), 11)
        self.assertEqual(self.estructural["cantidad_total_piezas"], 106)
        self.assertEqual(self.estructural["advertencias"], [])

    def test_valores_conocidos(self):
        vigas_pergola = [p for p in self.estructural["piezas"] if p.descripcion == "Vigas de pergola"]
        self.assertEqual(len(vigas_pergola), 1)
        pieza = vigas_pergola[0]
        self.assertEqual(pieza.cantidad, 2)
        self.assertEqual(pieza.ancho_mm, 90.0)
        self.assertEqual(pieza.alto_mm, 245.0)
        self.assertEqual(pieza.largo_m, 6.72)
        self.assertEqual(pieza.confianza, "alta")

    def test_columnas_duplicadas_se_conservan_ambas(self):
        columnas = [p for p in self.estructural["piezas"] if p.descripcion == "columna"]
        self.assertEqual(len(columnas), 2)

    def test_plano_arquitectonico_no_tiene_este_cuadro(self):
        # Disciplina/firma distinta -- confirma que el extractor no
        # inventa un cómputo donde no existe, y que lo dice explícitamente
        # (ver PRODUCTION_READINESS_REVIEW.md, hallazgo D1).
        self.assertEqual(self.arquitectonico["piezas"], [])
        self.assertEqual(self.arquitectonico["cantidad_total_piezas"], 0)
        self.assertEqual(len(self.arquitectonico["advertencias"]), 1)
        self.assertIn("no se encontraron piezas", self.arquitectonico["advertencias"][0])


if __name__ == "__main__":
    unittest.main()
