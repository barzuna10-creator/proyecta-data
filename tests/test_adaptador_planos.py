"""Pruebas para api/adaptador_planos.py -- la conversión de la salida de
lectura_planos a un dict compacto para guardar en proyectos.plano_analisis
(ver INTEGRACION_LECTURA_PLANOS_PROYECTO.md y
FLUJO_PRESUPUESTO_DESDE_PLANO_V1.md).

Puramente unitarias con dataclasses sintéticas -- no abre ningún PDF ni
toca la base de datos."""

import unittest

from api.adaptador_planos import construir_analisis_plano
from lectura_planos.modelo import (
    CuadroAcabados,
    CuadroPuertas,
    Espacio,
    EntradaIndice,
    Lamina,
    ModeloEdificio,
    Nivel,
    PiezaEstructural,
    Proyecto,
    TipoPdf,
)

CUADROS_VACIOS = {"puertas": [], "ventanas": [], "acabados": [], "advertencias": []}
COMPUTO_VACIO = {"piezas": [], "cantidad_total_piezas": 0, "advertencias": []}


def _lamina(codigo, nombre, numero_pagina):
    return Lamina(
        numero_pagina=numero_pagina, tipo_pdf=TipoPdf.VECTORIAL_CON_TEXTO,
        codigo=codigo, nombre=nombre, disciplina="arquitectonico",
    )


class PruebaConstruirAnalisisPlano(unittest.TestCase):
    def setUp(self):
        self.proyecto_leido = Proyecto(
            ruta_pdf="x.pdf", nombre="RESIDENCIA S+Q", cantidad_laminas=3, disciplina="mixto",
            laminas=[
                _lamina("A102", "PLANTA DE DISTRIBUCION ARQUITECTONICA N 0.0 M", 7),
                _lamina("A402", "PLANTA DE ACABADOS DE PAREDES Y PISOS N 0.0 M", 28),
                _lamina("A704", "DETALLES DE PUERTAS Y VENTANAS", 37),
                _lamina("A999", "LAMINA NO REFERENCIADA POR NINGUN NIVEL", 50),
            ],
            indice=[EntradaIndice(codigo="A102", nombre="PLANTA DE DISTRIBUCION ARQUITECTONICA N 0.0 M")],
            advertencias=["advertencia del lector"],
        )
        self.modelo = ModeloEdificio(
            proyecto_nombre="RESIDENCIA S+Q",
            niveles=[Nivel(nombre="N 0.0 M", elevacion=0.0, laminas=("A102", "A402"))],
            espacios=[
                Espacio(nombre="COCINA", nivel="N 0.0 M", pagina_fuente=7, texto_original="COCINA"),
            ],
            referencias_laminas=[],
            advertencias=["advertencia del modelo de edificio"],
        )

    def test_estructura_basica(self):
        analisis = construir_analisis_plano(self.proyecto_leido, self.modelo, CUADROS_VACIOS, COMPUTO_VACIO)
        self.assertEqual(analisis["proyecto_nombre"], "RESIDENCIA S+Q")
        self.assertEqual(analisis["cantidad_laminas"], 3)
        self.assertEqual(len(analisis["niveles"]), 1)
        self.assertEqual(analisis["niveles"][0]["laminas"], ["A102", "A402"])
        self.assertEqual(len(analisis["espacios"]), 1)
        self.assertEqual(analisis["espacios"][0]["nombre"], "COCINA")

    def test_solo_incluye_laminas_referenciadas(self):
        # A999 no pertenece a ningún nivel/espacio/cuadro -- no debe inflar el JSON guardado.
        analisis = construir_analisis_plano(self.proyecto_leido, self.modelo, CUADROS_VACIOS, COMPUTO_VACIO)
        self.assertIn("7", analisis["laminas"])  # A102
        self.assertIn("28", analisis["laminas"])  # A402
        self.assertNotIn("50", analisis["laminas"])  # A999

    def test_espacio_apunta_a_su_lamina_fuente_por_pagina(self):
        analisis = construir_analisis_plano(self.proyecto_leido, self.modelo, CUADROS_VACIOS, COMPUTO_VACIO)
        pagina = str(analisis["espacios"][0]["pagina_fuente"])
        lamina = analisis["laminas"][pagina]
        self.assertEqual(lamina["codigo"], "A102")
        self.assertEqual(lamina["disciplina"], "arquitectonico")
        self.assertEqual(lamina["tipo_pdf"], "vectorial_con_texto")

    def test_advertencias_combinadas_de_las_cuatro_fuentes(self):
        cuadros = {**CUADROS_VACIOS, "advertencias": ["advertencia de cuadros"]}
        computo = {**COMPUTO_VACIO, "advertencias": ["advertencia de computo"]}
        analisis = construir_analisis_plano(self.proyecto_leido, self.modelo, cuadros, computo)
        self.assertIn("advertencia del lector", analisis["advertencias"])
        self.assertIn("advertencia del modelo de edificio", analisis["advertencias"])
        self.assertIn("advertencia de cuadros", analisis["advertencias"])
        self.assertIn("advertencia de computo", analisis["advertencias"])

    def test_sin_niveles_ni_espacios_no_rompe(self):
        proyecto_vacio = Proyecto(
            ruta_pdf="x.pdf", nombre="x", cantidad_laminas=1, disciplina="sin_determinar",
            laminas=[_lamina(None, None, 1)], indice=None, advertencias=[],
        )
        modelo_vacio = ModeloEdificio(
            proyecto_nombre="x", niveles=[], espacios=[], referencias_laminas=[], advertencias=[],
        )
        analisis = construir_analisis_plano(proyecto_vacio, modelo_vacio, CUADROS_VACIOS, COMPUTO_VACIO)
        self.assertEqual(analisis["niveles"], [])
        self.assertEqual(analisis["espacios"], [])
        self.assertEqual(analisis["laminas"], {})

    def test_puerta_incluye_termino_busqueda_derivado_de_tipo_y_material(self):
        puerta = CuadroPuertas(
            codigo="P1", ancho=1.7, alto=2.9, tipo="pivotante", material="lamina de hn",
            cantidad=None, pagina_fuente=37, texto_original="PUERTA PIVOTANTE...", confianza="alta",
        )
        cuadros = {**CUADROS_VACIOS, "puertas": [puerta]}
        analisis = construir_analisis_plano(self.proyecto_leido, self.modelo, cuadros, COMPUTO_VACIO)
        self.assertEqual(len(analisis["puertas"]), 1)
        self.assertEqual(analisis["puertas"][0]["termino_busqueda"], "pivotante lamina de hn")
        self.assertIn("37", analisis["laminas"])  # A704, referenciada por la puerta

    def test_puerta_sin_tipo_ni_material_cae_al_texto_original(self):
        puerta = CuadroPuertas(
            codigo="P9", ancho=None, alto=None, tipo=None, material=None,
            cantidad=None, pagina_fuente=37, texto_original="PUERTA DE CUATRO PAÑOS...", confianza="alta",
        )
        cuadros = {**CUADROS_VACIOS, "puertas": [puerta]}
        analisis = construir_analisis_plano(self.proyecto_leido, self.modelo, cuadros, COMPUTO_VACIO)
        self.assertEqual(analisis["puertas"][0]["termino_busqueda"], "PUERTA DE CUATRO PAÑOS...")

    def test_acabado_usa_su_propia_descripcion_como_termino_busqueda(self):
        acabado = CuadroAcabados(
            codigo="1", descripcion="ACABADO CHUKUM", marca=None, formato=None, color="Crema Arena",
            ubicacion="muros_y_paredes", pagina_fuente=28, texto_original="1 | ACABADO CHUKUM", confianza="alta",
        )
        cuadros = {**CUADROS_VACIOS, "acabados": [acabado]}
        analisis = construir_analisis_plano(self.proyecto_leido, self.modelo, cuadros, COMPUTO_VACIO)
        self.assertEqual(analisis["acabados"][0]["termino_busqueda"], "ACABADO CHUKUM")

    def test_pieza_estructural_usa_su_descripcion_como_termino_busqueda(self):
        pieza = PiezaEstructural(
            cantidad=2, ancho_mm=90.0, alto_mm=245.0, largo_m=6.72, descripcion="Vigas de pergola",
            pagina_fuente=4, texto_original="2 Piezas de 90mm x 245mm x 6.72mts Vigas de pergola", confianza="alta",
        )
        computo = {**COMPUTO_VACIO, "piezas": [pieza]}
        analisis = construir_analisis_plano(self.proyecto_leido, self.modelo, CUADROS_VACIOS, computo)
        self.assertEqual(len(analisis["piezas_estructurales"]), 1)
        self.assertEqual(analisis["piezas_estructurales"][0]["termino_busqueda"], "Vigas de pergola")
        self.assertEqual(analisis["piezas_estructurales"][0]["cantidad"], 2)


if __name__ == "__main__":
    unittest.main()
