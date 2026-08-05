"""Adapta la salida de `lectura_planos` (dataclasses de
LECTURA_DE_PLANOS_V1_MVP/V2_CUADROS/V3_MODELO_EDIFICIO/
EXTRACTOR_COMPUTO_ESTRUCTURAL_V1) a un dict plano, listo para
`json.dumps` -- fuente única tanto de la navegación de solo lectura
(Proyecto -> Niveles -> Espacios -> Lámina fuente) como del primer flujo
de presupuesto (ver FLUJO_PRESUPUESTO_DESDE_PLANO_V1.md): cada material
candidato (puerta, ventana, acabado, pieza de estructura) ya trae un
`termino_busqueda` derivado de sus propios campos ya extraídos -- nunca
una técnica de extracción nueva, solo composición de lo que
`lectura_planos` ya midió y devolvió.

Deliberadamente NO vuelca todo lo que produce lectura_planos (cajetín
crudo de cada lámina, texto interno de extractores) -- solo lo que esta
integración necesita mostrar. Guardar menos que el modelo completo es una
decisión, no un descuido: mantiene la fila de `proyectos` liviana y evita
acoplar el esquema de la base de datos a la forma interna de
`lectura_planos`, que puede seguir creciendo sin romper esta integración.
"""


def _termino_busqueda_puerta_ventana(fila):
    partes = [p for p in (fila.tipo, fila.material) if p]
    return " ".join(partes) if partes else fila.texto_original


def _termino_busqueda_acabado(fila):
    return fila.descripcion or fila.texto_original


def _termino_busqueda_pieza(fila):
    return fila.descripcion


def construir_analisis_plano(proyecto_leido, modelo_edificio, cuadros, computo_estructural):
    codigos_relevantes = set()
    for nivel in modelo_edificio.niveles:
        codigos_relevantes.update(nivel.laminas)

    laminas_por_codigo = {l.codigo: l for l in proyecto_leido.laminas if l.codigo}
    paginas_relevantes = {
        laminas_por_codigo[codigo].numero_pagina
        for codigo in codigos_relevantes
        if codigo in laminas_por_codigo
    }
    # también cubrir cualquier página que por algún motivo no calzó con un
    # código de nivel (no debería pasar, pero no se oculta) -- incluye
    # ahora las páginas fuente de cuadros y del cómputo estructural, para
    # que todo material candidato pueda mostrar su lámina de origen.
    paginas_relevantes.update(e.pagina_fuente for e in modelo_edificio.espacios)
    paginas_relevantes.update(f.pagina_fuente for f in cuadros["puertas"])
    paginas_relevantes.update(f.pagina_fuente for f in cuadros["ventanas"])
    paginas_relevantes.update(f.pagina_fuente for f in cuadros["acabados"])
    paginas_relevantes.update(p.pagina_fuente for p in computo_estructural["piezas"])

    laminas = {}
    for lamina in proyecto_leido.laminas:
        if lamina.numero_pagina not in paginas_relevantes:
            continue
        laminas[str(lamina.numero_pagina)] = {
            "codigo": lamina.codigo,
            "nombre": lamina.nombre,
            "disciplina": lamina.disciplina,
            "tipo_pdf": lamina.tipo_pdf.value,
        }

    return {
        "proyecto_nombre": proyecto_leido.nombre,
        "cantidad_laminas": proyecto_leido.cantidad_laminas,
        "niveles": [
            {"nombre": n.nombre, "elevacion": n.elevacion, "laminas": list(n.laminas)}
            for n in modelo_edificio.niveles
        ],
        "espacios": [
            {
                "nombre": e.nombre,
                "nivel": e.nivel,
                "pagina_fuente": e.pagina_fuente,
                "texto_original": e.texto_original,
            }
            for e in modelo_edificio.espacios
        ],
        "puertas": [
            {
                "codigo": f.codigo,
                "ancho": f.ancho,
                "alto": f.alto,
                "tipo": f.tipo,
                "material": f.material,
                "cantidad": f.cantidad,
                "pagina_fuente": f.pagina_fuente,
                "texto_original": f.texto_original,
                "confianza": f.confianza,
                "termino_busqueda": _termino_busqueda_puerta_ventana(f),
            }
            for f in cuadros["puertas"]
        ],
        "ventanas": [
            {
                "codigo": f.codigo,
                "ancho": f.ancho,
                "alto": f.alto,
                "tipo": f.tipo,
                "material": f.material,
                "cantidad": f.cantidad,
                "pagina_fuente": f.pagina_fuente,
                "texto_original": f.texto_original,
                "confianza": f.confianza,
                "termino_busqueda": _termino_busqueda_puerta_ventana(f),
            }
            for f in cuadros["ventanas"]
        ],
        "acabados": [
            {
                "codigo": f.codigo,
                "descripcion": f.descripcion,
                "marca": f.marca,
                "formato": f.formato,
                "color": f.color,
                "ubicacion": f.ubicacion,
                "pagina_fuente": f.pagina_fuente,
                "texto_original": f.texto_original,
                "confianza": f.confianza,
                "termino_busqueda": _termino_busqueda_acabado(f),
            }
            for f in cuadros["acabados"]
        ],
        "piezas_estructurales": [
            {
                "cantidad": p.cantidad,
                "ancho_mm": p.ancho_mm,
                "alto_mm": p.alto_mm,
                "largo_m": p.largo_m,
                "descripcion": p.descripcion,
                "pagina_fuente": p.pagina_fuente,
                "texto_original": p.texto_original,
                "confianza": p.confianza,
                "termino_busqueda": _termino_busqueda_pieza(p),
            }
            for p in computo_estructural["piezas"]
        ],
        "laminas": laminas,
        "advertencias": (
            list(proyecto_leido.advertencias)
            + list(modelo_edificio.advertencias)
            + list(cuadros["advertencias"])
            + list(computo_estructural["advertencias"])
        ),
    }
