"""Pruebas para lectura_planos/modelo_edificio.py (LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO
-- ver LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO.md para la auditoría completa
y la justificación de qué se modela y qué se dejó fuera).

Mismo criterio que las fases anteriores: unitarias puras + integración
contra el plano arquitectónico real, que se salta si el archivo no está
presente."""

import os
import unittest

from lectura_planos.modelo_edificio import (
    PATRON_DETALLE,
    PATRON_NIVEL_EN_NOMBRE,
    PATRON_RUIDO_LETRAS_GRILLA,
    PATRON_SECCION,
    _derivar_niveles,
    _parece_nombre_espacio,
)
from lectura_planos.modelo import Lamina, Proyecto, TipoPdf

RUTA_PLANO_ESTRUCTURAL = "/Users/joseandresbarzuna/Downloads/2022-12-13 Planos taller peralon Rev.pdf"
RUTA_PLANO_ARQUITECTONICO = "/Users/joseandresbarzuna/Downloads/20250312 - Planos Arquitectonicos.pdf"
PLANOS_REALES_DISPONIBLES = os.path.exists(RUTA_PLANO_ESTRUCTURAL) and os.path.exists(RUTA_PLANO_ARQUITECTONICO)


def _lamina(codigo, nombre, numero_pagina=1):
    return Lamina(
        numero_pagina=numero_pagina, tipo_pdf=TipoPdf.VECTORIAL_CON_TEXTO,
        codigo=codigo, nombre=nombre, disciplina=None,
    )


class PruebaPatronNivel(unittest.TestCase):
    def test_reconoce_niveles_reales(self):
        casos = {
            "PLANTA DE DISTRIBUCION ARQUITECTONICA N 0.0 M": "0.0",
            "PLANTA DE CIELOS N -3.50 M": "-3.50",
            "PLANTA ARQUITECTONICA DE CUBIERTAS N +6.50 M": "+6.50",
        }
        for nombre, esperado in casos.items():
            m = PATRON_NIVEL_EN_NOMBRE.search(nombre)
            self.assertIsNotNone(m, nombre)
            self.assertEqual(m.group(1), esperado)

    def test_nombre_sin_nivel_no_calza(self):
        self.assertIsNone(PATRON_NIVEL_EN_NOMBRE.search("DETALLES DE PUERTAS Y VENTANAS"))


class PruebaDerivarNiveles(unittest.TestCase):
    def test_agrupa_laminas_por_elevacion_y_ordena(self):
        proyecto = Proyecto(
            ruta_pdf="x.pdf", nombre="x", cantidad_laminas=3, disciplina="mixto",
            laminas=[
                _lamina("A402", "PLANTA DE ACABADOS DE PAREDES Y PISOS N 0.0 M"),
                _lamina("A601", "PLANTA DE CIELOS N -3.50 M"),
                _lamina("A102", "PLANTA DE DISTRIBUCION ARQUITECTONICA N 0.0 M"),
            ],
            indice=None,
        )
        niveles = _derivar_niveles(proyecto)
        self.assertEqual([n.nombre for n in niveles], ["N -3.50 M", "N 0.0 M"])  # ordenado por elevación
        nivel_00 = next(n for n in niveles if n.elevacion == 0.0)
        self.assertEqual(set(nivel_00.laminas), {"A402", "A102"})

    def test_lamina_sin_nombre_no_rompe(self):
        proyecto = Proyecto(
            ruta_pdf="x.pdf", nombre="x", cantidad_laminas=1, disciplina="sin_determinar",
            laminas=[_lamina(None, None)], indice=None,
        )
        self.assertEqual(_derivar_niveles(proyecto), [])


class PruebaPatronesReferencia(unittest.TestCase):
    def test_seccion_real(self):
        m = PATRON_SECCION.match("A\nA301")
        self.assertEqual((m.group(1), m.group(2)), ("A", "A301"))

    def test_detalle_real(self):
        m = PATRON_DETALLE.match("DETALLE\n1\nA804")
        self.assertEqual((m.group(1), m.group(2)), ("1", "A804"))

    def test_texto_libre_no_calza(self):
        self.assertIsNone(PATRON_SECCION.match("COCINA"))
        self.assertIsNone(PATRON_DETALLE.match("DETALLE DE VENTANA"))


class PruebaFiltroNombreEspacio(unittest.TestCase):
    def test_acepta_nombres_reales(self):
        for nombre in ("COCINA", "AREA SOCIAL", "S.S. VISITAS", "CUARTO MAQUINAS"):
            self.assertTrue(_parece_nombre_espacio(nombre), nombre)

    def test_rechaza_cotas_y_notas_tecnicas(self):
        for texto in ("NPT 0.00", "NCT +2.90", "DETALLE 1", "SISA DE 2 cm", "BAÑO PRINCIPAL"):
            self.assertFalse(_parece_nombre_espacio(texto), texto)

    def test_ruido_de_grilla_se_filtra_aparte(self):
        # "D G" -- dos letras de marca de eje fundidas en un solo bloque de
        # texto -- pasa el filtro léxico pero se descarta con este patrón.
        self.assertTrue(_parece_nombre_espacio("D G"))  # no tiene dígitos ni palabra excluida
        self.assertTrue(PATRON_RUIDO_LETRAS_GRILLA.match("D G"))
        self.assertFalse(PATRON_RUIDO_LETRAS_GRILLA.match("AREA SOCIAL"))


@unittest.skipUnless(
    PLANOS_REALES_DISPONIBLES,
    "Requiere los dos planos reales de la auditoría en ~/Downloads (no están en el repositorio).",
)
class PruebaIntegracionModeloEdificioReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import lectura_planos as lp

        cls.lp = lp
        cls.estructural = lp.construir_modelo_edificio(lp.leer_proyecto(RUTA_PLANO_ESTRUCTURAL))
        cls.arquitectonico = lp.construir_modelo_edificio(lp.leer_proyecto(RUTA_PLANO_ARQUITECTONICO))

    def test_plano_sin_niveles_nombrados_no_produce_nada(self):
        # El plano de taller no usa la convención "N ... M" en sus nombres
        # de lámina -- confirmado, no es un fallo del extractor.
        self.assertEqual(self.estructural.niveles, [])
        self.assertEqual(self.estructural.espacios, [])
        self.assertEqual(self.estructural.referencias_laminas, [])

    def test_cuatro_niveles_reales(self):
        elevaciones = [n.elevacion for n in self.arquitectonico.niveles]
        self.assertEqual(elevaciones, sorted(elevaciones))  # ordenados
        self.assertEqual(elevaciones, [-3.5, 0.0, 3.5, 6.5])

    def test_espacios_conocidos_del_nivel_0(self):
        nombres_nivel_0 = {e.nombre for e in self.arquitectonico.espacios if e.nivel == "N 0.0 M"}
        for esperado in ("COCINA", "COMEDOR", "AREA SOCIAL", "GARAJE", "TERRAZA"):
            self.assertIn(esperado, nombres_nivel_0)

    def test_espacios_del_nivel_6_5_esta_vacio_honestamente(self):
        # A105/A604 (nivel +6.50) son "cubiertas"/"cielos", ninguna es la
        # lámina de distribución -- no hay catálogo de espacios ahí, y el
        # modelo no debe inventar uno.
        nombres_nivel_65 = [e for e in self.arquitectonico.espacios if e.nivel == "N +6.50 M"]
        self.assertEqual(nombres_nivel_65, [])

    def test_referencias_entre_laminas_apuntan_a_laminas_reales(self):
        self.assertGreater(len(self.arquitectonico.referencias_laminas), 0)
        for r in self.arquitectonico.referencias_laminas:
            self.assertTrue(r.destino_existe, f"{r.lamina_origen} -> {r.lamina_destino}")

    def test_referencia_conocida_de_seccion(self):
        referencias_a301 = [
            r for r in self.arquitectonico.referencias_laminas
            if r.lamina_origen == "A102" and r.lamina_destino == "A301"
        ]
        self.assertEqual(len(referencias_a301), 1)
        self.assertEqual(referencias_a301[0].tipo, "seccion")
        self.assertEqual(referencias_a301[0].identificador, "A")

    def test_advertencia_explica_por_que_faltan_acabados_y_puertas(self):
        texto_advertencias = " ".join(self.arquitectonico.advertencias)
        self.assertIn("acabados-por-espacio", texto_advertencias)
        self.assertIn("puertas/ventanas", texto_advertencias)


if __name__ == "__main__":
    unittest.main()
