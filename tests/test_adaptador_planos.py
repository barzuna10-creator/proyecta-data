"""Pruebas para api/adaptador_planos.py -- la conversión de la salida de
lectura_planos a un dict compacto para guardar en proyectos.plano_analisis
(ver INTEGRACION_LECTURA_PLANOS_PROYECTO.md).

Puramente unitarias con dataclasses sintéticas -- no abre ningún PDF ni
toca la base de datos."""

import unittest

from api.adaptador_planos import construir_analisis_plano
from lectura_planos.modelo import (
    Espacio,
    EntradaIndice,
    Lamina,
    ModeloEdificio,
    Nivel,
    Proyecto,
    TipoPdf,
)


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
        analisis = construir_analisis_plano(self.proyecto_leido, self.modelo)
        self.assertEqual(analisis["proyecto_nombre"], "RESIDENCIA S+Q")
        self.assertEqual(analisis["cantidad_laminas"], 3)
        self.assertEqual(len(analisis["niveles"]), 1)
        self.assertEqual(analisis["niveles"][0]["laminas"], ["A102", "A402"])
        self.assertEqual(len(analisis["espacios"]), 1)
        self.assertEqual(analisis["espacios"][0]["nombre"], "COCINA")

    def test_solo_incluye_laminas_referenciadas_por_un_nivel(self):
        # A999 no pertenece a ningún nivel -- no debe inflar el JSON guardado.
        analisis = construir_analisis_plano(self.proyecto_leido, self.modelo)
        self.assertIn("7", analisis["laminas"])  # A102
        self.assertIn("28", analisis["laminas"])  # A402
        self.assertNotIn("50", analisis["laminas"])  # A999

    def test_espacio_apunta_a_su_lamina_fuente_por_pagina(self):
        analisis = construir_analisis_plano(self.proyecto_leido, self.modelo)
        pagina = str(analisis["espacios"][0]["pagina_fuente"])
        lamina = analisis["laminas"][pagina]
        self.assertEqual(lamina["codigo"], "A102")
        self.assertEqual(lamina["disciplina"], "arquitectonico")
        self.assertEqual(lamina["tipo_pdf"], "vectorial_con_texto")

    def test_advertencias_combinadas_de_ambas_fuentes(self):
        analisis = construir_analisis_plano(self.proyecto_leido, self.modelo)
        self.assertIn("advertencia del lector", analisis["advertencias"])
        self.assertIn("advertencia del modelo de edificio", analisis["advertencias"])

    def test_sin_niveles_ni_espacios_no_rompe(self):
        proyecto_vacio = Proyecto(
            ruta_pdf="x.pdf", nombre="x", cantidad_laminas=1, disciplina="sin_determinar",
            laminas=[_lamina(None, None, 1)], indice=None, advertencias=[],
        )
        modelo_vacio = ModeloEdificio(
            proyecto_nombre="x", niveles=[], espacios=[], referencias_laminas=[], advertencias=[],
        )
        analisis = construir_analisis_plano(proyecto_vacio, modelo_vacio)
        self.assertEqual(analisis["niveles"], [])
        self.assertEqual(analisis["espacios"], [])
        self.assertEqual(analisis["laminas"], {})


if __name__ == "__main__":
    unittest.main()
