"""Pruebas para equivalencias.py. Varios casos acá no son hipotéticos --
son los hallazgos reales de calibrar el motor contra el catálogo completo
(ver EQUIVALENCIAS.md): el código corto que colisiona entre fabricantes
sin relación (SP02), el repuesto que comparte código con su producto
base sin ser el mismo artículo, y los estándares de industria (SCH40,
RJ45) que nunca deben tratarse como SKU de un producto puntual.
"""

import unittest

from equivalencias import (
    EQUIVALENCIA_CONFIRMADA,
    EQUIVALENCIA_PROBABLE,
    NO_EQUIVALENTE,
    UMBRALES_POR_MODULO,
    calcular_equivalencias,
    calcular_puntaje_equivalencia,
    comparar_atributos,
    es_equivalente_para,
    es_parte_o_accesorio,
    extraer_atributos,
    extraer_codigos,
    extraer_tokens_identidad,
)


class PruebaExtraerCodigos(unittest.TestCase):
    def test_codigo_real_con_letras_y_numeros(self):
        # Caso real: Ferretería Brenes y Construplaza, misma broca Dewalt.
        self.assertIn("DW5402", extraer_codigos("Broca SDS Plus 3/16 x 4 Dewalt DW5402"))

    def test_valor_de_especificacion_no_es_codigo(self):
        # "180W" empieza con dígito -- es una potencia, no un modelo. La
        # regla estructural (código empieza con letra) ya lo descarta acá,
        # sin necesitar una lista de unidades.
        self.assertNotIn("180W", extraer_codigos("Lijadora de vibración 180W"))

    def test_codigo_de_varias_letras_y_numeros(self):
        self.assertIn("STHT70150", extraer_codigos("Grapadora pesada Stanley STHT70150"))

    def test_nombre_vacio(self):
        self.assertEqual(extraer_codigos(""), set())
        self.assertEqual(extraer_codigos(None), set())


class PruebaTokensIdentidad(unittest.TestCase):
    def test_excluye_unidades_y_numeros(self):
        tokens = extraer_tokens_identidad('Taladro percutor 1/2" 750 W Daewoo')
        self.assertIn("taladro", tokens)
        self.assertIn("percutor", tokens)
        self.assertIn("daewoo", tokens)
        self.assertNotIn("w", tokens)
        self.assertNotIn("1", tokens)
        self.assertNotIn("2", tokens)


class PruebaEsParteOAccesorio(unittest.TestCase):
    def test_detecta_repuesto(self):
        tokens = extraer_tokens_identidad("Repuesto carbon para demoledor ULDBRH1301")
        self.assertTrue(es_parte_o_accesorio(tokens))

    def test_producto_base_no_es_parte(self):
        tokens = extraer_tokens_identidad("Demoledor industrial 1300W Emtop ULDBRH1301")
        self.assertFalse(es_parte_o_accesorio(tokens))


class PruebaCompararAtributos(unittest.TestCase):
    def test_codigo_largo_compartido_confirma(self):
        # Caso real: misma broca Dewalt DW5402, dos proveedores.
        a = extraer_atributos("Broca SDS Plus 3/16 x 4 Dewalt DW5402", "Dewalt")
        b = extraer_atributos("DW Broca p/rotomartillo 3/16*4 DW5402", None)
        veredicto, razones, _ = comparar_atributos(a, b)
        self.assertEqual(veredicto, EQUIVALENCIA_CONFIRMADA)
        self.assertTrue(any("codigo_compartido" in r for r in razones))

    def test_codigo_corto_sin_refuerzo_es_solo_probable(self):
        # Caso real encontrado calibrando: "SP02" coincide por casualidad
        # entre un dispensador de jabón y un soplete de gas -- productos
        # sin ninguna relación. Sin marca ni tokens compartidos de
        # refuerzo, un código de 4 caracteres no alcanza para confirmar.
        a = extraer_atributos("Dispensador jabon liquido ABS negro SP02", None)
        b = extraer_atributos("Soplete mechero industrial 3 salidas Floppi FL-SP02", "Floppi")
        veredicto, _, _ = comparar_atributos(a, b)
        self.assertNotEqual(veredicto, EQUIVALENCIA_CONFIRMADA)

    def test_repuesto_vs_producto_base_no_confirma_aunque_compartan_codigo(self):
        # Caso real: un repuesto y su herramienta base comparten el mismo
        # código de modelo, pero no son el mismo artículo comprable.
        base = extraer_atributos("Demoledor industrial 1300W Emtop ULDBRH1301", "Emtop")
        repuesto = extraer_atributos("Repuesto carbon para demoledor ULDBRH1301 Emtop", "Emtop")
        veredicto, razones, _ = comparar_atributos(base, repuesto)
        self.assertNotEqual(veredicto, EQUIVALENCIA_CONFIRMADA)
        self.assertIn("asimetria_repuesto_vs_producto_base", razones)

    def test_especificacion_en_conflicto_nunca_confirma(self):
        a = extraer_atributos('Taladro percutor 1/2" 750W Daewoo', "Daewoo")
        b = extraer_atributos('Taladro percutor 3/8" 750W Daewoo', "Daewoo")
        veredicto, razones, _ = comparar_atributos(a, b)
        self.assertEqual(veredicto, NO_EQUIVALENTE)
        self.assertTrue(any("specs_en_conflicto" in r for r in razones))

    def test_volumen_distinto_nunca_confirma(self):
        # "galon" y "litro" los reconoce el extractor genérico de volumen
        # (a diferencia de "cuarto"/"cubeta", jerga de presentación de
        # pintura que solo entiende extraer_presentacion_pintura -- ver
        # test_presentacion_pintura_distinta_no_confirma para ese caso).
        a = extraer_atributos("Thinner corriente 1 galon", "Sur")
        b = extraer_atributos("Thinner corriente 1 litro", "Sur")
        veredicto, razones, _ = comparar_atributos(a, b)
        self.assertEqual(veredicto, NO_EQUIVALENTE)
        self.assertTrue(any("specs_en_conflicto" in r for r in razones))

    def test_fraccion_de_galon_entre_parentesis_se_reconoce_como_presentacion(self):
        # Bug real encontrado corriendo el motor completo: Ferretería
        # Brenes escribe la presentación como "(1/4)"/"(GLN)" en vez de
        # la palabra "Cuarto"/"Galón" -- sin reconocer esa notación, dos
        # tamaños DISTINTOS del mismo barniz Lanco terminaban en el mismo
        # grupo de equivalencia.
        cuarto = extraer_atributos("Barniz Poliuretano Mate Cuarto Lanco", "Lanco", categoria="Pinturas")
        galon_brenes = extraer_atributos(
            "LANCO BARNIZ POLIURETANO MATE PV361 (GLN)", "Lanco", categoria="Pinturas"
        )
        veredicto, razones, _ = comparar_atributos(cuarto, galon_brenes)
        self.assertEqual(veredicto, NO_EQUIVALENTE)
        self.assertTrue(any("presentacion_distinta" in r for r in razones))

    def test_presentacion_pintura_distinta_no_confirma(self):
        a = extraer_atributos("Pintura Latex 3000 Mate Blanco Galon Sur", "Sur", categoria="Pinturas")
        b = extraer_atributos("Pintura Latex 3000 Mate Blanco Cuarto Sur", "Sur", categoria="Pinturas")
        veredicto, razones, _ = comparar_atributos(a, b)
        self.assertEqual(veredicto, NO_EQUIVALENTE)
        self.assertTrue(any("presentacion_distinta" in r for r in razones))

    def test_numero_de_linea_o_codigo_no_se_confunde_con_presentacion(self):
        # Bug real encontrado corriendo el motor completo: Construplaza
        # escribe "Corrostop 9000 galon Sur 0900070006" -- familias.py
        # trata CUALQUIER número suelto del nombre como parte de la
        # presentación (correcto para tamaños de empaque como "1"/"5"
        # galones), así que "9000" (línea) y "0900070006" (código
        # interno) se colaban en la etiqueta ("9000 Galón 0900070006"),
        # que nunca coincidía con la etiqueta limpia de otro proveedor
        # ("Galón") y bloqueaba una equivalencia real.
        el_lagar = extraer_atributos(
            "Pintura Anticorrosiva Corrostop Negro Galon Sur", "SUR", categoria="Pinturas"
        )
        construplaza = extraer_atributos(
            "Pintura anticorrosiva Corrostop 9000 galon Sur 0900070006 (negro)",
            "Sur",
            categoria="Pinturas",
        )
        veredicto, _, _ = comparar_atributos(el_lagar, construplaza)
        self.assertEqual(veredicto, EQUIVALENCIA_CONFIRMADA)

    def test_color_distinto_con_palabra_de_acabado_compartida_no_confirma(self):
        # Bug real encontrado corriendo el motor completo: "Rojo Óxido" y
        # "Verde Óxido" comparten "óxido" (acabado, no color) además de
        # marca/línea/presentación -- eso solo empuja el jaccard de
        # tokens por encima del umbral de confirmación aunque sean dos
        # colores distintos, nunca el mismo producto comprable. A
        # diferencia de test_misma_marca_mismo_producto_distinto_color_no_confirma
        # (donde el color es la única palabra que no coincide), acá hace
        # falta una palabra compartida de más para exponer el bug.
        rojo = extraer_atributos(
            "Pintura anticorrosiva Corrostop 9000 cubeta Sur 0900030905 (rojo oxido)",
            "Sur",
            categoria="Pinturas",
        )
        verde = extraer_atributos(
            "Pintura anticorrosiva Corrostop 9000 cubeta Sur 0900066505 (verde oxido)",
            "Sur",
            categoria="Pinturas",
        )
        veredicto, razones, _ = comparar_atributos(rojo, verde)
        self.assertEqual(veredicto, NO_EQUIVALENTE)
        self.assertTrue(any("color_distinto" in r for r in razones))

    def test_mismo_color_no_bloquea_equivalencia(self):
        rojo_a = extraer_atributos(
            "Pintura anticorrosiva Corrostop 9000 cubeta Sur 0900030905 (rojo oxido)",
            "Sur",
            categoria="Pinturas",
        )
        rojo_b = extraer_atributos(
            "Pintura Anticorrosiva Corrostop Rojo Oxido Cubeta Sur", "SUR", categoria="Pinturas"
        )
        veredicto, _, _ = comparar_atributos(rojo_a, rojo_b)
        self.assertEqual(veredicto, EQUIVALENCIA_CONFIRMADA)

    def test_marfil_y_negro_son_colores_distintos(self):
        # Bug real encontrado en la auditoría (ver EQUIVALENCIAS.md):
        # "marfil"/"champagne" no estaban en COLORES -- interruptores
        # eléctricos negro/marfil/champagne son tan comunes en este
        # catálogo como blanco/negro, pero al no reconocerse "marfil" el
        # chequeo de color quedaba asimétrico (nunca bloqueaba) y un
        # código de fabricante sin el sufijo de color ("UL6501" en vez de
        # "UL6501-BK"/"UL6501-V") bastaba para confirmar por su cuenta.
        negro = extraer_atributos(
            "Interruptor sencillo 15A 220V negro Decor Eagle UL6501-BK", "Eagle", categoria="Iluminación y Ventilación"
        )
        marfil = extraer_atributos(
            "Interruptor sencillo 15A 220V marfil Decor Eagle UL6501-V", "Eagle", categoria="Iluminación y Ventilación"
        )
        veredicto, razones, _ = comparar_atributos(negro, marfil)
        self.assertEqual(veredicto, NO_EQUIVALENTE)
        self.assertTrue(any("color_distinto" in r for r in razones))

    def test_codigo_corto_de_familia_con_marca_igual_no_confirma_solo(self):
        # Bug real encontrado corriendo el motor completo: "N400" es el
        # prefijo de TODA la línea de bisagras National Hardware (decenas
        # de tamaños y acabados distintos), no el SKU de un producto
        # puntual -- compartirlo, más marca igual, alcanzaba para
        # confirmar sin ninguna palabra de refuerzo real, mezclando
        # tamaños/acabados distintos de la misma marca.
        cromo = extraer_atributos(
            'Bisagra de puerta 4" x 4" cromo (zinc) National Hardware N400-013 (2 por paquete)',
            "National Hardware",
            categoria="Puertas y Cerrajería",
        )
        satinado = extraer_atributos(
            'Bisagra de puerta 4" x 4" bronce satinado 512 National Hardware N400-039 (2 por paquete)',
            "National Hardware",
            categoria="Puertas y Cerrajería",
        )
        veredicto, _, _ = comparar_atributos(cromo, satinado)
        self.assertNotEqual(veredicto, EQUIVALENCIA_CONFIRMADA)

    def test_marca_de_varias_palabras_se_excluye_completa_de_tokens_de_refuerzo(self):
        # Antes de este fix, solo se excluía el string completo de la
        # marca ("black & decker") de tokens_compartidos, que nunca calza
        # contra palabras sueltas ("black", "decker") -- cada palabra de
        # una marca de dos o más palabras seguía contando como token de
        # refuerzo "independiente" para cualquier par de esa marca, sin
        # ninguna otra palabra en común real (dos productos distintos,
        # sopladora vs. taladro).
        sopladora = extraer_atributos("Sopladora Black & Decker BV3600", "Black & Decker")
        taladro = extraer_atributos("Taladro Black & Decker XYZ999", "Black & Decker")
        veredicto, _, _ = comparar_atributos(sopladora, taladro)
        self.assertNotEqual(veredicto, EQUIVALENCIA_CONFIRMADA)

    def test_fraccion_de_galon_suelta_sin_parentesis_distingue_tamanos(self):
        # Bug real encontrado corriendo el motor completo: "Masilla Wood
        # Filler" -- Construplaza escribe "1/16 galon"/"1/32 galon"
        # (que familias.py etiqueta igual, "Galón", porque no entiende
        # fracciones) y Brenes escribe la fracción sola sin unidad ni
        # paréntesis ("WF828-5  1/4") -- sin este fix, los 4 tamaños
        # (1/32, 1/16, 1/8, 1/4 de galón) se agrupaban como si fueran el
        # mismo producto.
        dieciseisavo = extraer_atributos(
            "Masilla para madera Wood Filler 1/16 galon Lanco WF828-7 (caoba)", "Lanco", categoria="Pinturas"
        )
        cuarto = extraer_atributos("LANCO MASILLA P/ MADERA MAHOGANY WF828-5  1/4", None, categoria="Pinturas")
        veredicto, razones, _ = comparar_atributos(dieciseisavo, cuarto)
        self.assertEqual(veredicto, NO_EQUIVALENTE)
        self.assertTrue(any("presentacion_distinta" in r for r in razones))

    def test_misma_marca_y_dos_tokens_confirma_sin_codigo(self):
        a = extraer_atributos("Cemento Fuerte saco 50 kg Holcim (gris)", "Holcim")
        b = extraer_atributos("Holcim - cemento fuerte saco 50 kg", "Holcim")
        veredicto, _, _ = comparar_atributos(a, b)
        self.assertEqual(veredicto, EQUIVALENCIA_CONFIRMADA)

    def test_misma_marca_pero_productos_distintos_no_confirma(self):
        # Bug real encontrado corriendo el motor sobre el catálogo
        # completo: "marca igual + 2 tokens" agrupó 1,154 herramientas
        # Truper DISTINTAS en un solo grupo, porque cualquier par
        # comparte "truper" (contado como marca Y como token) más una
        # palabra genérica de categoría ("alicate"). Dos herramientas
        # Truper genuinamente distintas nunca deben confirmarse solo por
        # eso.
        a = extraer_atributos('Alicate Articulado 10" Truper', "Truper")
        b = extraer_atributos('Alicate Corte Diagonal 5" Truper', "Truper")
        veredicto, _, _ = comparar_atributos(a, b)
        self.assertNotEqual(veredicto, EQUIVALENCIA_CONFIRMADA)

    def test_misma_marca_mismo_producto_distinto_color_no_confirma(self):
        # Mismo bug, variante encontrada en Pinturas: dos colores
        # distintos de la misma línea ("Aquavar") comparten marca +
        # "barniz"/"aquavar" -- pero Caoba y Nuez no son el mismo barniz.
        a = extraer_atributos("Barniz Aquavar Caoba Galon Lanco", "Lanco")
        b = extraer_atributos("Barniz Aquavar Nuez Galon Lanco", "Lanco")
        veredicto, _, _ = comparar_atributos(a, b)
        self.assertNotEqual(veredicto, EQUIVALENCIA_CONFIRMADA)

    def test_codigo_generico_no_cuenta_como_token_de_refuerzo(self):
        # Bug real encontrado corriendo el motor completo: "MR11" (forma
        # de bombillo, estándar de la industria) se descartaba bien del
        # canal de código por frecuencia, pero seguía colando como token
        # normal -- dos bombillos de watts y temperatura de color
        # totalmente distintos confirmaban solo por compartir
        # "bombillo,led,mr11" como tokens.
        a = extraer_atributos("Bombillo led 3 W G5.3 MR11 3.000 K ECOMAX", None)
        b = extraer_atributos(
            "Bombillo LED GU10 MR11 4W 127-220V 3000K 250lm Blumenau 02041013", None
        )
        veredicto, _, _ = comparar_atributos(a, b, codigos_genericos=frozenset({"MR11", "GU10"}))
        self.assertNotEqual(veredicto, EQUIVALENCIA_CONFIRMADA)

    def test_sin_marca_ni_codigo_exige_mucha_evidencia_de_texto(self):
        a = extraer_atributos("Tornillo gypsum negro punta broca 6 x 1-1/4", None)
        b = extraer_atributos("Tornillo gypsum negro punta fina 6 x 1", None)
        veredicto, _, _ = comparar_atributos(a, b)
        self.assertNotEqual(veredicto, EQUIVALENCIA_CONFIRMADA)

    def test_nombres_sin_relacion_no_equivalen(self):
        a = extraer_atributos("Martillo de uña 16 oz mango fibra", "Truper")
        b = extraer_atributos("Inodoro elongado blanco 2 piezas", "Novex")
        veredicto, _, _ = comparar_atributos(a, b)
        self.assertEqual(veredicto, NO_EQUIVALENTE)


class PruebaCalcularEquivalencias(unittest.TestCase):
    def _producto(self, id_, proveedor, nombre, marca=None, categoria="Herramientas"):
        return {"id": id_, "proveedor": proveedor, "nombre": nombre, "marca": marca, "categoria": categoria}

    def test_agrupa_dos_proveedores_por_codigo_real(self):
        productos = [
            self._producto(1, "Construplaza", "Broca SDS Plus 3/16 x 4 Dewalt DW5402", "Dewalt"),
            self._producto(2, "Ferretería Brenes", "DW Broca p/rotomartillo 3/16*4 DW5402"),
            self._producto(3, "EPA", "Escalera de aluminio plegable 6 pasos"),
        ]
        grupos = calcular_equivalencias(productos)
        self.assertEqual(len(grupos), 1)
        ids_en_grupo = {m["id"] for m in grupos[0]["miembros"]}
        self.assertEqual(ids_en_grupo, {1, 2})

    def test_nunca_agrupa_dos_productos_del_mismo_proveedor(self):
        productos = [
            self._producto(1, "Construplaza", "Broca SDS Plus 3/16 x 4 Dewalt DW5402", "Dewalt"),
            self._producto(2, "Construplaza", "Broca SDS Plus 3/16 x 4 Dewalt DW5402 (repuesto)", "Dewalt"),
        ]
        grupos = calcular_equivalencias(productos)
        self.assertEqual(grupos, [])

    def test_codigo_generico_de_industria_no_agrupa_por_frecuencia(self):
        # Simula un código que aparece en decenas de productos (como
        # SCH40 o RJ45 en el catálogo real) -- no debe agrupar nada por
        # sí solo, aunque el patrón estructural lo detecte como "código".
        productos = [
            self._producto(i, "EPA" if i % 2 == 0 else "Ferretería Brenes", f"Conector conduit UL SCH40 tipo {i}")
            for i in range(1, 40)
        ]
        grupos = calcular_equivalencias(productos)
        self.assertEqual(grupos, [])

    def test_transitividad_agrupa_tres_proveedores(self):
        # A equivale a B (por código), B equivale a C (por código) -- deben
        # terminar en el mismo grupo aunque A y C nunca se comparen
        # directo con evidencia propia.
        productos = [
            self._producto(1, "Construplaza", "Percha negro mate Triva Moen BP1803BL", "Moen"),
            self._producto(2, "Ferretería Brenes", "Moen perchero sencillo Triva BP1803BL negro"),
            self._producto(3, "Novex", "Perchero Triva BP1803BL Moen acabado negro", "Moen"),
        ]
        grupos = calcular_equivalencias(productos)
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]["cantidad_miembros"], 3)
        self.assertEqual(grupos[0]["cantidad_proveedores"], 3)


class PruebaCalcularPuntajeEquivalencia(unittest.TestCase):
    """calcular_puntaje_equivalencia() responde la misma pregunta que
    comparar_atributos() (arriba, sin tocar) pero con un puntaje continuo
    [0, 1] y explicable en vez de un veredicto de tres niveles -- ver el
    diseño completo en MOTOR_CONFIANZA.md. Los casos acá son los mismos
    hallazgos reales ya usados para calibrar comparar_atributos: deben
    seguir dando el resultado correcto con la nueva forma de combinar
    señales, no es una calibración desde cero."""

    def test_estructura_del_resultado(self):
        a = extraer_atributos("Broca SDS Plus 3/16 x 4 Dewalt DW5402", "Dewalt")
        b = extraer_atributos("DW Broca p/rotomartillo 3/16*4 DW5402", None)
        resultado = calcular_puntaje_equivalencia(a, b)
        self.assertIn("puntaje", resultado)
        self.assertIn("veto", resultado)
        self.assertIn("señales", resultado)
        self.assertIn("explicacion", resultado)
        for nombre in (
            "marca", "sku", "codigo_fabricante", "color", "tamano",
            "presentacion", "volumen", "dimensiones", "tokens", "categoria",
        ):
            self.assertIn(nombre, resultado["señales"], f"falta la señal '{nombre}' en el resultado")
            self.assertIn("valor", resultado["señales"][nombre])
            self.assertIn("conflicto", resultado["señales"][nombre])
            self.assertIn("detalle", resultado["señales"][nombre])
        self.assertGreaterEqual(resultado["puntaje"], 0.0)
        self.assertLessEqual(resultado["puntaje"], 1.0)

    def test_codigo_fabricante_alto_pese_a_tokens_debiles(self):
        # Caso real: mismo DW5402, descrito con vocabulario casi sin
        # solapamiento ("SDS Plus...Dewalt" vs "p/rotomartillo") -- un
        # promedio ponderado plano diluiría el código de fabricante hasta
        # un puntaje mediocre; acá debe quedar cerca de 1.0 (ver
        # SEÑAL_ANCLA en equivalencias.py para el porqué del diseño).
        a = extraer_atributos("Broca SDS Plus 3/16 x 4 Dewalt DW5402", "Dewalt")
        b = extraer_atributos("DW Broca p/rotomartillo 3/16*4 DW5402", None)
        resultado = calcular_puntaje_equivalencia(a, b)
        self.assertGreaterEqual(resultado["puntaje"], 0.85)
        self.assertIsNone(resultado["veto"])

    def test_codigo_corto_sin_refuerzo_queda_bajo(self):
        # Caso real: "SP02" coincide por casualidad entre un dispensador
        # de jabón y un soplete de gas -- sin marca ni tokens de refuerzo,
        # el puntaje debe quedar claramente por debajo de cualquier
        # umbral razonable.
        a = extraer_atributos("Dispensador jabon liquido ABS negro SP02", None)
        b = extraer_atributos("Soplete mechero industrial 3 salidas Floppi FL-SP02", "Floppi")
        resultado = calcular_puntaje_equivalencia(a, b)
        self.assertLess(resultado["puntaje"], 0.5)

    def test_veto_por_dimension_fisica(self):
        a = extraer_atributos('Taladro percutor mandril 1/2"', "Bosch")
        b = extraer_atributos('Taladro percutor mandril 3/8"', "Bosch")
        resultado = calcular_puntaje_equivalencia(a, b)
        self.assertEqual(resultado["puntaje"], 0.0)
        self.assertEqual(resultado["veto"], "dimensiones")

    def test_veto_por_color(self):
        a = extraer_atributos(
            "Pintura anticorrosiva Corrostop 9000 cubeta Sur (rojo oxido)", "Sur", categoria="Pinturas"
        )
        b = extraer_atributos(
            "Pintura anticorrosiva Corrostop 9000 cubeta Sur (verde oxido)", "Sur", categoria="Pinturas"
        )
        resultado = calcular_puntaje_equivalencia(a, b)
        self.assertEqual(resultado["puntaje"], 0.0)
        self.assertEqual(resultado["veto"], "color")

    def test_veto_por_presentacion(self):
        cuarto = extraer_atributos("Barniz Poliuretano Mate Cuarto Lanco", "Lanco", categoria="Pinturas")
        galon = extraer_atributos("LANCO BARNIZ POLIURETANO MATE PV361 (GLN)", "Lanco", categoria="Pinturas")
        resultado = calcular_puntaje_equivalencia(cuarto, galon)
        self.assertEqual(resultado["puntaje"], 0.0)
        self.assertEqual(resultado["veto"], "presentacion")

    def test_veto_por_volumen(self):
        a = extraer_atributos("Thinner corriente 1 galon", "Sur")
        b = extraer_atributos("Thinner corriente 1 litro", "Sur")
        resultado = calcular_puntaje_equivalencia(a, b)
        self.assertEqual(resultado["puntaje"], 0.0)
        self.assertEqual(resultado["veto"], "volumen")

    def test_diametro_en_pulgadas_vs_milimetros_coincide(self):
        # Hallazgo real de la auditoría: un proveedor mide en pulgadas y
        # otro en milímetros para el mismo tamaño nominal (escuadras,
        # candados, tubería PVC) -- especificaciones.py nunca los
        # comparaba entre sí. Acá sí, con tolerancia para tallas
        # nominales redondeadas (1/2" real son 12.7 mm).
        a = extraer_atributos('Escuadra de refuerzo 1/2" galvanizado', "National Hardware")
        b = extraer_atributos("Escuadra de refuerzo 12 mm galvanizado", "National Hardware")
        resultado = calcular_puntaje_equivalencia(a, b)
        self.assertIsNone(resultado["veto"])
        self.assertEqual(resultado["señales"]["dimensiones"]["valor"], 1.0)

    def test_diametro_en_pulgadas_vs_milimetros_en_conflicto(self):
        a = extraer_atributos('Escuadra de refuerzo 1/2" galvanizado', "National Hardware")
        b = extraer_atributos("Escuadra de refuerzo 63 mm galvanizado", "National Hardware")
        resultado = calcular_puntaje_equivalencia(a, b)
        self.assertEqual(resultado["puntaje"], 0.0)
        self.assertEqual(resultado["veto"], "dimensiones")

    def test_ausencia_no_es_conflicto(self):
        # Si ninguno de los dos lados tiene marca, la señal "marca" no
        # debe participar del puntaje (ni sumar ni restar) -- ausencia de
        # evidencia no es evidencia de ausencia.
        a = extraer_atributos("Cemento Fuerte saco 50 kg Holcim (gris)", None)
        b = extraer_atributos("Holcim - cemento fuerte saco 50 kg", None)
        resultado = calcular_puntaje_equivalencia(a, b)
        self.assertIsNone(resultado["señales"]["marca"]["valor"])

    def test_asimetria_repuesto_reduce_el_puntaje(self):
        repuesto = extraer_atributos("Repuesto carbon para demoledor ULDBRH1301", "Total")
        base = extraer_atributos("Demoledor ULDBRH1301", "Total")
        base_otro_proveedor = extraer_atributos("Demoledor martillo ULDBRH1301 Total", "Total")

        con_asimetria = calcular_puntaje_equivalencia(repuesto, base)
        sin_asimetria = calcular_puntaje_equivalencia(base, base_otro_proveedor)

        self.assertIn("ajuste_repuesto", con_asimetria["señales"])
        self.assertNotIn("ajuste_repuesto", sin_asimetria["señales"])
        self.assertLess(con_asimetria["puntaje"], sin_asimetria["puntaje"])

    def test_explicacion_es_texto_no_vacio(self):
        a = extraer_atributos("Broca SDS Plus 3/16 x 4 Dewalt DW5402", "Dewalt")
        b = extraer_atributos("DW Broca p/rotomartillo 3/16*4 DW5402", None)
        resultado = calcular_puntaje_equivalencia(a, b)
        self.assertIsInstance(resultado["explicacion"], str)
        self.assertGreater(len(resultado["explicacion"]), 0)
        self.assertIn("DW5402", resultado["explicacion"])


class PruebaUmbralesPorModulo(unittest.TestCase):
    def test_todos_los_modulos_pedidos_tienen_umbral(self):
        for modulo in ("busqueda", "similares", "comparador", "presupuestos", "cotizaciones"):
            self.assertIn(modulo, UMBRALES_POR_MODULO)

    def test_es_equivalente_para_respeta_el_umbral(self):
        umbral = UMBRALES_POR_MODULO["comparador"]
        self.assertTrue(es_equivalente_para("comparador", umbral))
        self.assertFalse(es_equivalente_para("comparador", umbral - 0.01))

    def test_modulo_desconocido_lanza_error(self):
        with self.assertRaises(ValueError):
            es_equivalente_para("modulo_inventado", 0.9)

    def test_presupuestos_y_cotizaciones_son_mas_exigentes_que_busqueda(self):
        # Un falso positivo en presupuestos/cotizaciones implica dinero
        # real; en búsqueda es, como mucho, un resultado de más.
        self.assertGreater(UMBRALES_POR_MODULO["presupuestos"], UMBRALES_POR_MODULO["busqueda"])
        self.assertGreater(UMBRALES_POR_MODULO["cotizaciones"], UMBRALES_POR_MODULO["busqueda"])


if __name__ == "__main__":
    unittest.main()
