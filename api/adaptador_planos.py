"""Adapta la salida de `lectura_planos` (dataclasses de
LECTURA_DE_PLANOS_V1_MVP/V2_CUADROS/V3_MODELO_EDIFICIO) a un dict plano,
listo para `json.dumps` y para el árbol de navegación del frontend
(Proyecto -> Niveles -> Espacios -> Lámina fuente).

Deliberadamente NO vuelca todo lo que produce lectura_planos (cajetín
crudo de cada lámina, cuadros de acabados/puertas/ventanas, texto interno
de extractores) -- solo lo que esta integración necesita mostrar. Guardar
menos que el modelo completo es una decisión, no un descuido: mantiene la
fila de `proyectos` liviana y evita acoplar el esquema de la base de
datos a la forma interna de `lectura_planos`, que puede seguir creciendo
(fases geométricas futuras) sin romper esta integración."""


def construir_analisis_plano(proyecto_leido, modelo_edificio):
    codigos_relevantes = set()
    for nivel in modelo_edificio.niveles:
        codigos_relevantes.update(nivel.laminas)

    laminas_por_codigo = {l.codigo: l for l in proyecto_leido.laminas if l.codigo}
    paginas_de_espacios = {
        laminas_por_codigo[codigo].numero_pagina
        for codigo in codigos_relevantes
        if codigo in laminas_por_codigo
    }
    # también cubrir cualquier página de espacio que por algún motivo no
    # calzó con un código de nivel (no debería pasar, pero no se oculta)
    paginas_de_espacios.update(e.pagina_fuente for e in modelo_edificio.espacios)

    laminas = {}
    for lamina in proyecto_leido.laminas:
        if lamina.numero_pagina not in paginas_de_espacios:
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
        "laminas": laminas,
        "advertencias": list(proyecto_leido.advertencias) + list(modelo_edificio.advertencias),
    }
