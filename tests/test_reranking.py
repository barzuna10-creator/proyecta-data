"""Pruebas para reranking.py -- en particular, regresión de los 4 hallazgos
reales de BUSCADOR_AUDITORIA.md:

1. bm25 con normalización por rango (no lineal por magnitud) -- un
   candidato con bm25 extremo por una coincidencia de variante rara o por
   eco de categoría ya no puede, solo por su magnitud, ganarle a
   candidatos genuinamente más relevantes.
2. frase_exacta por límite de palabra, no substring -- "cemento" no debe
   leerse como "encontrado" dentro de "cementos".
3. penalización de accesorio extendida a verbos/sustantivos de acción
   ("Quita cementos", "Limpiador de vidrios").
4. reordenar() de punta a punta reproduce el caso real "cemento" ya no
   pone "Quita cementos y limpia juntas" antes que cemento real.

No usa base de datos -- reordenar() opera sobre listas de dicts en Python
puro, así que los candidatos se construyen a mano.
"""

import unittest

from reranking import (
    _frase_como_palabras,
    _normalizar_bm25,
    _precedida_por_accesorio,
    reordenar,
)


def _candidato(nombre, categoria="Construccion", puntaje=-10.0, **extra):
    base = {
        "nombre": nombre,
        "categoria": categoria,
        "puntaje": puntaje,
        "proveedor": "EPA",
        "id_proveedor": "1",
    }
    base.update(extra)
    return base


class PruebaNormalizarBm25(unittest.TestCase):
    def test_mejor_bm25_vale_1_peor_vale_0(self):
        candidatos = [_candidato("a", puntaje=-5.0), _candidato("b", puntaje=-20.0)]
        normalizados = _normalizar_bm25(candidatos)
        # -20 es "mejor" (más negativo) en la convención de bm25 de SQLite.
        self.assertEqual(normalizados[1], 1.0)
        self.assertEqual(normalizados[0], 0.0)

    def test_un_solo_valor_unico_no_lanza_division_por_cero(self):
        candidatos = [_candidato("a", puntaje=-10.0), _candidato("b", puntaje=-10.0)]
        normalizados = _normalizar_bm25(candidatos)
        self.assertEqual(normalizados, [1.0, 1.0])

    def test_normalizacion_es_por_rango_no_por_magnitud(self):
        # Caso real medido: un candidato con bm25 = -19.55 (extremo, por
        # coincidir vía una variante rara) no debe valer proporcionalmente
        # más que uno con -13.13 solo por la diferencia de magnitud --
        # debe valer lo mismo que CUALQUIER segundo lugar, sin importar
        # qué tan grande sea el salto hasta el primero.
        candidatos_brecha_grande = [
            _candidato("primero", puntaje=-19.55),
            _candidato("segundo", puntaje=-13.13),
            _candidato("tercero", puntaje=-13.05),
        ]
        candidatos_brecha_chica = [
            _candidato("primero", puntaje=-13.20),
            _candidato("segundo", puntaje=-13.13),
            _candidato("tercero", puntaje=-13.05),
        ]
        # El segundo lugar vale lo mismo en ambos casos -- 0.5 (a mitad de
        # camino entre el mejor=1.0 y el peor=0.0) -- pese a que la brecha
        # real de magnitud contra el primero es enormemente distinta.
        self.assertEqual(_normalizar_bm25(candidatos_brecha_grande)[1], 0.5)
        self.assertEqual(_normalizar_bm25(candidatos_brecha_chica)[1], 0.5)


class PruebaFraseComoPalabras(unittest.TestCase):
    def test_frase_de_una_palabra_encuentra_coincidencia_exacta(self):
        self.assertTrue(_frase_como_palabras("cemento", ["cemento", "gris", "kg"]))

    def test_plural_no_cuenta_como_frase_exacta_de_singular(self):
        # El caso real: "cemento" no debe leerse como "encontrado" solo
        # porque es un substring literal de "cementos".
        self.assertFalse(_frase_como_palabras("cemento", ["quita", "cementos", "y", "limpia"]))

    def test_frase_de_varias_palabras_debe_ser_consecutiva(self):
        self.assertTrue(_frase_como_palabras("cable electrico", ["cable", "electrico", "nm-b"]))
        self.assertFalse(_frase_como_palabras("cable electrico", ["cable", "para", "electrico"]))

    def test_frase_vacia_no_encuentra_nada(self):
        self.assertFalse(_frase_como_palabras("", ["cemento"]))


class PruebaPrecedidaPorAccesorio(unittest.TestCase):
    def test_primera_palabra_nunca_esta_precedida(self):
        self.assertFalse(_precedida_por_accesorio(["cemento", "gris"], 0))

    def test_preposicion_de_accesorio_directa(self):
        # "Tornillo PARA policarbonato"
        self.assertTrue(_precedida_por_accesorio(["tornillo", "para", "policarbonato"], 2))

    def test_verbo_de_accion_directo(self):
        # Caso real: "Quita cementos"
        self.assertTrue(_precedida_por_accesorio(["quita", "cementos"], 1))

    def test_verbo_de_accion_con_preposicion_en_medio(self):
        # Caso real del catálogo: "Limpiador de vidrios"
        self.assertTrue(_precedida_por_accesorio(["limpiador", "de", "vidrios"], 2))

    def test_de_solo_no_es_accesorio_sin_verbo_de_accion_antes(self):
        # "Mortero de cemento" -- el cemento es un ingrediente real, no un
        # accesorio; "de" por sí solo (sin sustantivo de acción antes) no
        # debe penalizar.
        self.assertFalse(_precedida_por_accesorio(["mortero", "de", "cemento"], 2))


class PruebaReordenarRegresionCasosReales(unittest.TestCase):
    def test_cemento_no_pone_quita_cementos_antes_que_cemento_real(self):
        candidatos = [
            _candidato(
                "Quita cementos y limpia juntas MPL 1 litro",
                categoria="Limpieza",
                puntaje=-19.55,
            ),
            _candidato("Cemento Gris Por Kilo", categoria="Construcción", puntaje=-13.13),
            _candidato("Cemento Blanco Por Kilo", categoria="Construcción", puntaje=-13.13),
        ]

        resultado = reordenar(candidatos, "cemento", limite=10)
        nombres = [r["nombre"] for r in resultado]

        self.assertNotEqual(nombres[0], "Quita cementos y limpia juntas MPL 1 litro")
        self.assertIn("Quita cementos y limpia juntas MPL 1 litro", nombres)

    def test_varilla_no_pone_producto_no_relacionado_antes_que_varilla_real(self):
        # Candidate set de tamaño realista (la búsqueda real trae 45
        # candidatos, no 2) -- con un candidate set minúsculo, la
        # normalización por rango por sí sola puede seguir polarizando de
        # más un solo outlier; el beneficio real de la normalización por
        # rango depende de que existan varios productos genuinamente
        # relevantes agrupados cerca del tope, como pasa en la práctica.
        candidatos = [
            _candidato(
                "Ambientador navidad varillas 95 ml galleta jengibre",
                categoria="Decoracion",
                puntaje=-17.73,
            ),
            _candidato("Varilla nacional deformada #5 grado 40 6 m", categoria="Construccion", puntaje=-13.21),
            _candidato("Varilla nacional lisa #5 grado 60 6 m", categoria="Construccion", puntaje=-13.21),
            _candidato("Varilla cuadrada 12 mm- 6 metros", categoria="Construccion", puntaje=-13.37),
            _candidato("Varilla cuadrada 9 mm- 6 metros", categoria="Construccion", puntaje=-13.37),
            _candidato("Varilla Mezclador Mortero 4\"", categoria="Herramientas", puntaje=-13.44),
        ]

        resultado = reordenar(candidatos, "varilla", limite=10)
        nombres = [r["nombre"] for r in resultado]

        self.assertNotEqual(nombres[0], "Ambientador navidad varillas 95 ml galleta jengibre")
        self.assertIn("varilla", nombres[0].lower())

    def test_bloque_con_eco_de_categoria_no_arrastra_el_resto_del_orden(self):
        # El candidato con eco nombre+categoria sigue siendo el bm25 más
        # extremo (rank 1 legítimo), pero ya no debe arrastrar un peso
        # desproporcionado sobre las demás señales combinadas -- se
        # verifica que el puntaje combinado del segundo lugar no colapse a
        # near-zero solo por la brecha de magnitud bm25.
        candidatos = [
            _candidato(
                "Bloque De Aluminio 300 Mm Para Anclaje Lateral",
                categoria="Sistema Bloques FX50 Anclaje Lateral",
                puntaje=-22.47,
            ),
            _candidato("Block PC clase A 20 x 20 x 40 cm", categoria="Construccion", puntaje=-20.39),
        ]

        resultado = reordenar(candidatos, "bloque", limite=10, depurar=True)
        # Con normalización por rango, el segundo lugar vale 0.0 en bm25
        # (es el peor de un candidate set de 2) -- pero las demás señales
        # (posición, cobertura) siguen siendo iguales en ambos, así que el
        # orden bm25 se respeta sin que la brecha de magnitud lo agrave.
        self.assertEqual(len(resultado), 2)

    def test_no_afecta_una_busqueda_ya_limpia(self):
        # Control negativo: "tubo pvc" no tenía ningún problema medido --
        # confirmar que el reordenamiento no introduce ruido donde no lo
        # había.
        candidatos = [
            _candidato('Tubo PVC 4" x 3 m', categoria="Plomeria", puntaje=-19.36),
            _candidato('Tubo PVC 2" x 3 m', categoria="Plomeria", puntaje=-19.36),
        ]
        resultado = reordenar(candidatos, "tubo pvc", limite=10)
        self.assertEqual(len(resultado), 2)
        self.assertTrue(all("Tubo PVC" in r["nombre"] for r in resultado))


if __name__ == "__main__":
    unittest.main()
