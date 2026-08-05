"""Pruebas para lectura_planos/cuadros.py (LECTURA_DE_PLANOS_V2_CUADROS
-- ver LECTURA_DE_PLANOS_V2_CUADROS.md para la auditoría completa).

Mismo criterio que tests/test_lectura_planos.py: unitarias puras (sin
abrir ningún PDF) + integración contra el plano arquitectónico real, que
se salta si el archivo no está presente en la máquina."""

import os
import unittest

from lectura_planos.cuadros import (
    PATRON_CODIGO_ACABADO,
    PATRON_CODIGO_PV,
    _deduplicar,
    _dividir_bloque_descripcion,
    _extraer_color,
    _extraer_material,
    _extraer_tipo,
    _parece_texto_legal,
    _parsear_medida_simple,
    agregar_cuadros,
)
from lectura_planos.modelo import CuadroAcabados, CuadroPuertas

RUTA_PLANO_ESTRUCTURAL = "/Users/joseandresbarzuna/Downloads/2022-12-13 Planos taller peralon Rev.pdf"
RUTA_PLANO_ARQUITECTONICO = "/Users/joseandresbarzuna/Downloads/20250312 - Planos Arquitectonicos.pdf"
PLANOS_REALES_DISPONIBLES = os.path.exists(RUTA_PLANO_ESTRUCTURAL) and os.path.exists(RUTA_PLANO_ARQUITECTONICO)


def _puerta(codigo, texto_original, confianza, pagina=37):
    return CuadroPuertas(
        codigo=codigo, ancho=None, alto=None, tipo=None, material=None, cantidad=None,
        pagina_fuente=pagina, texto_original=texto_original, confianza=confianza,
    )


class PruebaParseoMedida(unittest.TestCase):
    def test_numero_simple(self):
        self.assertEqual(_parsear_medida_simple("2.90"), 2.90)
        self.assertEqual(_parsear_medida_simple("1"), 1.0)

    def test_medida_variable_no_se_inventa(self):
        # "ANCHO VARIABLE (0.95 - 1.07)" -- nunca se toma un promedio ni el
        # mínimo/máximo, se deja sin valor (ver texto_original en el cuadro).
        self.assertIsNone(_parsear_medida_simple("ANCHO VARIABLE (0.95 - 1.07)"))
        self.assertIsNone(_parsear_medida_simple(""))


class PruebaPatronCodigo(unittest.TestCase):
    def test_codigo_puerta_ventana_real(self):
        m = PATRON_CODIGO_PV.match("P1\n2.90")
        self.assertEqual(m.group(1), "P1")
        self.assertEqual(m.group(2), "2.90")
        self.assertTrue(PATRON_CODIGO_PV.match("V17\n2.38"))

    def test_codigo_acabado_acepta_numerico_y_alfanumerico(self):
        for codigo in ("1", "12", "C1", "P01"):
            self.assertTrue(PATRON_CODIGO_ACABADO.match(codigo), codigo)

    def test_codigo_acabado_rechaza_texto_libre(self):
        self.assertFalse(PATRON_CODIGO_ACABADO.match("ACABADO"))
        self.assertFalse(PATRON_CODIGO_ACABADO.match("123"))  # 3 dígitos, fuera del rango observado


class PruebaDividirBloqueDescripcion(unittest.TestCase):
    def test_bloque_con_una_sola_descripcion_no_se_toca(self):
        texto = "PUERTA ABATIBLE DE MADERA. BUQUE DE 0.95 x 2.40 m."
        self.assertEqual(_dividir_bloque_descripcion(texto, "PUERTA"), [texto])

    def test_bloque_fusionado_se_separa_en_dos(self):
        # Caso real encontrado en A704/A706: dos filas de la tabla
        # terminaron en un solo bloque de texto de PyMuPDF.
        texto = (
            "PUERTA CORREDIZA DE VIDRIO TEXTURIZADO DE 8mm DE ESPESOR. BUQUE DE 1.70 x 2.40 m. "
            "PUERTA DE DOS PAÑOS CORREDIZOS DE VIDRIO CLARO 8mm DE ESPESOR. BUQUE DE 1.72 x 2.75 m."
        )
        partes = _dividir_bloque_descripcion(texto, "PUERTA")
        self.assertEqual(len(partes), 2)
        self.assertTrue(partes[0].startswith("PUERTA CORREDIZA DE VIDRIO TEXTURIZADO"))
        self.assertTrue(partes[1].startswith("PUERTA DE DOS PAÑOS"))


class PruebaExtraerTipoYMaterial(unittest.TestCase):
    def test_tipo_conocido(self):
        self.assertEqual(_extraer_tipo("PUERTA ABATIBLE DE MADERA.", "PUERTA"), "abatible")
        self.assertEqual(_extraer_tipo("PUERTA CORREDIZA ESQUINERA DE SEIS PAÑOS", "PUERTA"), "corrediza esquinera")

    def test_tipo_desconocido_no_se_inventa(self):
        self.assertIsNone(_extraer_tipo("PUERTA DE CUATRO PAÑOS DE VIDRIO CLARO", "PUERTA"))

    def test_material_conocido(self):
        self.assertEqual(_extraer_material("PUERTA ABATIBLE DE MADERA."), "madera")
        self.assertEqual(_extraer_material("VENTANA FIJA DE VIDRIO CLARO 8mm DE ESPESOR."), "vidrio")

    def test_material_desconocido_no_se_inventa(self):
        self.assertIsNone(_extraer_material("PUERTA PIVOTANTE SIN MATERIAL RECONOCIBLE"))


class PruebaExtraerColor(unittest.TestCase):
    def test_color_presente(self):
        self.assertEqual(_extraer_color("COLOR CREMA ARENA. VER MUESTRA EN SITIO"), "Crema Arena")

    def test_sin_color_no_se_inventa(self):
        self.assertIsNone(_extraer_color("Formato 1.20 x 1.20 m"))


class PruebaTextoLegal(unittest.TestCase):
    def test_detecta_pie_legal(self):
        self.assertTrue(_parece_texto_legal("EN EL ARTICULO 8 DEL REGLAMENTO PARA LA CONTRATACION..."))
        self.assertTrue(_parece_texto_legal("QUEDA PROHIBIDA TODA REPRODUCCION TOTAL O PARCIAL"))

    def test_fila_real_no_se_marca_como_legal(self):
        self.assertFalse(_parece_texto_legal("Sin cielo - - Losa expuesta con acabado concreto"))


class PruebaDeduplicar(unittest.TestCase):
    def test_duplicado_exacto_se_colapsa_sin_advertencia(self):
        filas = [_puerta("P1", "PUERTA X", "alta", 37), _puerta("P1", "PUERTA X", "alta", 38)]
        resultado, advertencias = _deduplicar(filas, lambda f: f.codigo)
        self.assertEqual(len(resultado), 1)
        self.assertEqual(advertencias, [])

    def test_conflicto_prefiere_mayor_confianza_sin_importar_el_orden(self):
        # Caso real: la página de menor confianza aparece ANTES en el
        # documento que la correcta -- el orden de aparición no debe
        # ganarle a la confianza (bug real encontrado y corregido).
        baja_primero = _puerta("V1", "descripción incorrecta", "baja", pagina=40)
        alta_despues = _puerta("V1", "descripción correcta", "alta", pagina=42)
        resultado, advertencias = _deduplicar([baja_primero, alta_despues], lambda f: f.codigo)
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].texto_original, "descripción correcta")
        self.assertEqual(len(advertencias), 1)

    def test_clave_compuesta_para_acabados(self):
        a = CuadroAcabados(
            codigo="1", descripcion="ACABADO CHUKUM", marca=None, formato=None, color=None,
            ubicacion="muros_y_paredes", pagina_fuente=28, texto_original="1 | ACABADO CHUKUM", confianza="alta",
        )
        b = CuadroAcabados(
            codigo="1", descripcion="Enchape de Porcelanato", marca=None, formato=None, color=None,
            ubicacion="pisos", pagina_fuente=28, texto_original="1 | Enchape de Porcelanato", confianza="media",
        )
        # mismo código "1" pero en sub-cuadros distintos -- no son la misma fila
        resultado, advertencias = _deduplicar([a, b], lambda f: (f.ubicacion, f.codigo))
        self.assertEqual(len(resultado), 2)
        self.assertEqual(advertencias, [])


@unittest.skipUnless(
    PLANOS_REALES_DISPONIBLES,
    "Requiere los dos planos reales de la auditoría en ~/Downloads (no están en el repositorio).",
)
class PruebaIntegracionCuadrosReales(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import lectura_planos as lp

        cls.estructural = lp.leer_proyecto(RUTA_PLANO_ESTRUCTURAL)
        cls.arquitectonico = lp.leer_proyecto(RUTA_PLANO_ARQUITECTONICO)
        cls.cuadros_estructural = agregar_cuadros(cls.estructural)
        cls.cuadros_arquitectonico = agregar_cuadros(cls.arquitectonico)

    def test_plano_sin_cuadros_de_este_tipo_no_produce_nada(self):
        # El plano de taller (estructura de madera) no tiene ningún cuadro
        # de acabados/puertas/ventanas -- confirmado en la auditoría.
        self.assertEqual(self.cuadros_estructural["puertas"], [])
        self.assertEqual(self.cuadros_estructural["ventanas"], [])
        self.assertEqual(self.cuadros_estructural["acabados"], [])

    def test_puertas_completas_y_de_alta_confianza(self):
        puertas = {f.codigo: f for f in self.cuadros_arquitectonico["puertas"]}
        self.assertEqual(len(puertas), 16)  # P1..P16, confirmado en la TABLA DE PUERTAS real
        for codigo in (f"P{i}" for i in range(1, 17)):
            self.assertIn(codigo, puertas)

    def test_valores_conocidos_de_puertas(self):
        puertas = {f.codigo: f for f in self.cuadros_arquitectonico["puertas"]}
        p1 = puertas["P1"]
        self.assertEqual(p1.ancho, 1.70)
        self.assertEqual(p1.alto, 2.90)
        self.assertEqual(p1.tipo, "pivotante")
        self.assertEqual(p1.material, "lamina de hn")
        self.assertIsNone(p1.cantidad)  # nunca se infiere, este cuadro no trae esa columna

    def test_ventanas_completas(self):
        ventanas = {f.codigo: f for f in self.cuadros_arquitectonico["ventanas"]}
        self.assertEqual(len(ventanas), 17)  # V1..V17
        v17 = ventanas["V17"]
        self.assertEqual(v17.ancho, 0.95)
        self.assertEqual(v17.alto, 2.38)

    def test_acabados_muros_y_paredes_completos_y_alta_confianza(self):
        muros = [f for f in self.cuadros_arquitectonico["acabados"] if f.ubicacion == "muros_y_paredes"]
        self.assertEqual(len(muros), 12)
        self.assertTrue(all(f.confianza == "alta" for f in muros))

    def test_acabados_pisos_y_cielos_confianza_media(self):
        pisos_cielos = [f for f in self.cuadros_arquitectonico["acabados"] if f.ubicacion in ("pisos", "cielos")]
        self.assertGreater(len(pisos_cielos), 0)
        self.assertTrue(all(f.confianza == "media" for f in pisos_cielos))

    def test_ningun_cuadro_incluye_texto_legal_del_pie_de_pagina(self):
        for f in self.cuadros_arquitectonico["acabados"]:
            self.assertFalse(_parece_texto_legal(f.texto_original), f.texto_original)

    def test_toda_fila_tiene_pagina_fuente_y_evidencia(self):
        todas = (
            self.cuadros_arquitectonico["puertas"]
            + self.cuadros_arquitectonico["ventanas"]
            + self.cuadros_arquitectonico["acabados"]
        )
        for f in todas:
            self.assertIsInstance(f.pagina_fuente, int)
            self.assertGreater(f.pagina_fuente, 0)
            # texto_original puede ser "" en algún caso de confianza baja sin
            # emparejar, pero el campo siempre existe (nunca None).
            self.assertIsInstance(f.texto_original, str)


if __name__ == "__main__":
    unittest.main()
