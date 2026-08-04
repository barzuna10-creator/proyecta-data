"""Pruebas para sistemas_constructivos.py: el modelo de datos en sí
(cálculo de rendimientos, relaciones derivadas, composición de
subsistemas) y una verificación de integración de que cada término de
búsqueda de cada material devuelve resultados reales del catálogo (mismo
criterio que ya se usó para PLANTILLAS_PROYECTO en el frontend)."""

import unittest

from busqueda import buscar_fts
import sistemas_constructivos as sc


class PruebaReglaRendimiento(unittest.TestCase):
    def test_calculo_directo_redondea_hacia_arriba_por_defecto(self):
        regla = sc.ReglaRendimiento(cantidad_por_unidad=12.5, merma=1.05)
        # 12.5 * 20 * 1.05 = 262.5 -> no se puede comprar medio bloque
        self.assertEqual(regla.calcular(20), 263)

    def test_calculo_continuo_no_redondea_si_se_pide(self):
        regla = sc.ReglaRendimiento(cantidad_por_unidad=0.2, redondear_entero=False)
        self.assertEqual(regla.calcular(10), 2.0)

    def test_fijo_ignora_la_cantidad_base(self):
        regla = sc.ReglaRendimiento(cantidad_por_unidad=1, fijo=True)
        self.assertEqual(regla.calcular(4), 1)
        self.assertEqual(regla.calcular(400), 1)


class PruebaUsoSubsistemaValidacion(unittest.TestCase):
    def test_exige_exactamente_uno_de_factor_o_cantidad_fija(self):
        with self.assertRaises(ValueError):
            sc.UsoSubsistema(uso_id="x", sistema_id="piso_ceramico", descripcion="")
        with self.assertRaises(ValueError):
            sc.UsoSubsistema(uso_id="x", sistema_id="piso_ceramico", descripcion="", factor=1.0, cantidad_fija=1.0)

    def test_uno_solo_es_valido(self):
        sc.UsoSubsistema(uso_id="x", sistema_id="piso_ceramico", descripcion="", factor=1.0)
        sc.UsoSubsistema(uso_id="y", sistema_id="piso_ceramico", descripcion="", cantidad_fija=1.0)


class PruebaRegistro(unittest.TestCase):
    def test_no_permite_id_duplicado(self):
        with self.assertRaises(ValueError):
            sc.registrar(sc.Sistema(id="muro_block", nombre="x", descripcion="", dimension=sc.Dimension.AREA_M2))

    def test_los_8_tipos_pedidos_estan_cubiertos(self):
        # baño, cocina, tapia, techo, piso, muro, instalación sanitaria,
        # instalación eléctrica -- techo y muro se cubren con más de un
        # sistema cada uno (ver sus docstrings de por qué).
        esperados = {
            "bano", "cocina", "tapia",
            "techo_lamina", "techo_cumbrera",
            "piso_ceramico",
            "muro_block", "muro_gypsum",
            "instalacion_sanitaria_basica",
            "instalacion_electrica_basica",
        }
        self.assertEqual(esperados, set(sc.REGISTRO.keys()))


class PruebaMuroBlock(unittest.TestCase):
    def test_bloque_cemento_y_arena_derivada(self):
        lineas = sc.calcular_materiales("muro_block", 20)
        por_id = {l.material_id: l for l in lineas}

        self.assertEqual(por_id["bloque"].cantidad, 263)  # 12.5*20*1.05 = 262.5 -> 263
        self.assertEqual(por_id["cemento_pega"].cantidad, 3)  # 0.11*20 = 2.2 -> 3
        # La arena se deriva del cemento YA REDONDEADO (3 sacos), no del
        # valor teórico 2.2 -- es lo que realmente se va a comprar.
        self.assertAlmostEqual(por_id["arena_pega"].cantidad, round(3 * 0.12, 2))

    def test_arena_cambia_si_cambia_el_cemento_calculado(self):
        # Confirma que la relación es real (deriva del resultado), no una
        # copia fija del mismo número para las dos áreas de prueba.
        lineas_chico = sc.calcular_materiales("muro_block", 5)
        lineas_grande = sc.calcular_materiales("muro_block", 50)
        arena_chico = next(l.cantidad for l in lineas_chico if l.material_id == "arena_pega")
        arena_grande = next(l.cantidad for l in lineas_grande if l.material_id == "arena_pega")
        self.assertLess(arena_chico, arena_grande)


class PruebaPisoCeramico(unittest.TestCase):
    def test_rendimiento_directo(self):
        lineas = sc.calcular_materiales("piso_ceramico", 10)
        por_id = {l.material_id: l for l in lineas}
        self.assertAlmostEqual(por_id["ceramica"].cantidad, 11.0)  # 10*1.10
        self.assertAlmostEqual(por_id["pegamento"].cantidad, 2.0)  # 10*0.2
        self.assertAlmostEqual(por_id["fragua"].cantidad, 4.0)  # 10*0.4


class PruebaTechoDosDimensiones(unittest.TestCase):
    def test_lamina_es_area_cumbrera_es_longitud(self):
        self.assertEqual(sc.REGISTRO["techo_lamina"].dimension, sc.Dimension.AREA_M2)
        self.assertEqual(sc.REGISTRO["techo_cumbrera"].dimension, sc.Dimension.LONGITUD_M)

    def test_calculo_independiente(self):
        area = sc.calcular_materiales("techo_lamina", 40)
        longitud = sc.calcular_materiales("techo_cumbrera", 8)
        self.assertTrue(any(l.material_id == "lamina" for l in area))
        self.assertTrue(any(l.material_id == "cumbrera" for l in longitud))


class PruebaInstalacionesBasicas(unittest.TestCase):
    def test_sanitaria_marca_opcionales(self):
        lineas = sc.calcular_materiales("instalacion_sanitaria_basica", 1)
        opcionales = {l.material_id for l in lineas if l.opcional}
        self.assertEqual(opcionales, {"llave_paso"})

    def test_electrica_tomacorriente_y_apagador_son_opcionales(self):
        lineas = sc.calcular_materiales("instalacion_electrica_basica", 1)
        opcionales = {l.material_id for l in lineas if l.opcional}
        self.assertEqual(opcionales, {"tomacorriente", "apagador"})

    def test_breaker_es_fraccion_no_uno_a_uno(self):
        lineas = sc.calcular_materiales("instalacion_electrica_basica", 1)
        breaker = next(l for l in lineas if l.material_id == "breaker")
        self.assertLess(breaker.cantidad, 1)


class PruebaTapiaReutilizaMuroBlock(unittest.TestCase):
    def test_incluye_materiales_de_muro_block(self):
        lineas = sc.calcular_materiales("tapia", 30)
        ids = {l.material_id for l in lineas}
        self.assertIn("bloque", ids)
        self.assertIn("cemento_pega", ids)
        self.assertIn("varilla_refuerzo", ids)

    def test_bloque_escala_con_el_area_de_la_tapia_no_con_un_valor_fijo(self):
        # Bug real encontrado probando esto: antes de corregir
        # UsoSubsistema, el subsistema de un sistema compuesto usaba
        # siempre su propio valor por defecto sin importar qué `cantidad`
        # recibiera calcular_materiales() -- una tapia de 100 m² pedía
        # los mismos bloques que una de 10 m².
        chica = sc.calcular_materiales("tapia", 10)
        grande = sc.calcular_materiales("tapia", 100)
        bloque_chica = next(l.cantidad for l in chica if l.material_id == "bloque")
        bloque_grande = next(l.cantidad for l in grande if l.material_id == "bloque")
        self.assertGreater(bloque_grande, bloque_chica * 5)


class PruebaBanoComponeDosVecesElMismoSubsistema(unittest.TestCase):
    def test_piso_y_pared_son_lineas_distintas_con_contexto_distinto(self):
        lineas = sc.calcular_materiales("bano", 4)
        ceramica = [l for l in lineas if l.material_id == "ceramica"]
        self.assertEqual(len(ceramica), 2)
        contextos = {l.contexto for l in ceramica}
        self.assertEqual(len(contextos), 2)  # piso y pared, nunca el mismo contexto

    def test_pared_escala_2_2x_el_area_de_piso(self):
        lineas = sc.calcular_materiales("bano", 4)
        ceramica = sorted((l.cantidad for l in lineas if l.material_id == "ceramica"))
        piso, pared = ceramica
        self.assertAlmostEqual(pared, piso * 2.2, places=1)

    def test_artefactos_fijos_no_escalan_con_el_area(self):
        chico = sc.calcular_materiales("bano", 3)
        grande = sc.calcular_materiales("bano", 8)
        inodoros_chico = sum(l.cantidad for l in chico if l.material_id == "inodoro")
        inodoros_grande = sum(l.cantidad for l in grande if l.material_id == "inodoro")
        self.assertEqual(inodoros_chico, inodoros_grande)
        self.assertEqual(inodoros_chico, 1)

    def test_piso_si_escala_con_el_area(self):
        chico = sc.calcular_materiales("bano", 3)
        grande = sc.calcular_materiales("bano", 8)
        ceramica_chico = min(l.cantidad for l in chico if l.material_id == "ceramica")
        ceramica_grande = min(l.cantidad for l in grande if l.material_id == "ceramica")
        self.assertGreater(ceramica_grande, ceramica_chico)

    def test_override_reemplaza_solo_el_uso_indicado(self):
        base = sc.calcular_materiales("bano", 4)
        piso_base = min(l.cantidad for l in base if l.material_id == "ceramica")

        con_override = sc.calcular_materiales("bano", 4, overrides={"pared_enchapada": 20.0})
        ceramicas = sorted((l.cantidad for l in con_override if l.material_id == "ceramica"))
        piso_nuevo, pared_nueva = ceramicas

        self.assertAlmostEqual(piso_nuevo, piso_base)  # el piso no cambió
        self.assertAlmostEqual(pared_nueva, 20.0 * 1.10)  # la pared sí, con su propia merma


class PruebaCocina(unittest.TestCase):
    def test_incluye_piso_y_artefactos_fijos(self):
        lineas = sc.calcular_materiales("cocina", 6)
        ids = {l.material_id for l in lineas}
        self.assertIn("ceramica", ids)
        self.assertIn("mueble_cocina", ids)
        self.assertIn("fregadero", ids)

    def test_electrica_no_escala_con_el_area(self):
        chica = sc.calcular_materiales("cocina", 4)
        grande = sc.calcular_materiales("cocina", 12)
        cable_chica = sum(l.cantidad for l in chica if l.material_id == "cable")
        cable_grande = sum(l.cantidad for l in grande if l.material_id == "cable")
        self.assertEqual(cable_chica, cable_grande)


class PruebaTerminosDeBusquedaContraElCatalogoReal(unittest.TestCase):
    """Mismo criterio que PLANTILLAS_PROYECTO en el frontend: cada
    término de búsqueda se prueba contra busqueda.buscar_fts() real, no
    contra un mock -- así un término que deja de funcionar porque el
    catálogo cambió se detecta acá, no en producción."""

    def test_todos_los_materiales_de_todos_los_sistemas_devuelven_resultados(self):
        sin_resultados = []
        for sistema in sc.REGISTRO.values():
            for material in sistema.materiales:
                resultados = buscar_fts(material.termino_busqueda, limite=1)
                if not resultados:
                    sin_resultados.append((sistema.id, material.id, material.termino_busqueda))

        self.assertEqual(
            sin_resultados, [],
            f"términos de búsqueda sin resultados reales: {sin_resultados}",
        )


if __name__ == "__main__":
    unittest.main()
