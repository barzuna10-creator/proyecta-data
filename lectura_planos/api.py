"""API mínima de consulta sobre un Proyecto ya leído (ver nucleo.leer_proyecto).

Deliberadamente de solo lectura: nada acá modifica el Proyecto ni vuelve a
tocar el PDF. Cuando existan extractores especializados (acabados,
puertas, ventanas, áreas), sus datos van a vivir en Lamina.extras -- estas
funciones no necesitan cambiar; solo se agregan funciones de consulta
nuevas al lado, siguiendo el mismo patrón.
"""

from .modelo import Lamina, Proyecto


def buscar_lamina(proyecto: Proyecto, codigo: str) -> Lamina | None:
    for lamina in proyecto.laminas:
        if lamina.codigo == codigo:
            return lamina
    return None


def lamina_por_pagina(proyecto: Proyecto, numero_pagina: int) -> Lamina | None:
    for lamina in proyecto.laminas:
        if lamina.numero_pagina == numero_pagina:
            return lamina
    return None


def laminas_por_disciplina(proyecto: Proyecto, disciplina: str) -> list:
    return [lamina for lamina in proyecto.laminas if lamina.disciplina == disciplina]


def laminas_sin_codigo(proyecto: Proyecto) -> list:
    return [lamina for lamina in proyecto.laminas if lamina.codigo is None]


def laminas_sin_disciplina(proyecto: Proyecto) -> list:
    return [lamina for lamina in proyecto.laminas if lamina.disciplina is None]


def resumen(proyecto: Proyecto) -> dict:
    """Resumen apto para mostrarle a un humano: conteos por disciplina y
    cuántas láminas quedaron sin código/nombre/disciplina -- nunca oculta
    lo que no se pudo determinar."""
    conteo_disciplinas: dict = {}
    for lamina in proyecto.laminas:
        clave = lamina.disciplina or "sin_determinar"
        conteo_disciplinas[clave] = conteo_disciplinas.get(clave, 0) + 1

    return {
        "nombre": proyecto.nombre,
        "cantidad_laminas": proyecto.cantidad_laminas,
        "disciplina": proyecto.disciplina,
        "tiene_indice": proyecto.indice is not None,
        "laminas_por_disciplina": conteo_disciplinas,
        "laminas_sin_codigo": len(laminas_sin_codigo(proyecto)),
        "laminas_sin_nombre": sum(1 for l in proyecto.laminas if l.nombre is None),
        "cantidad_advertencias": len(proyecto.advertencias),
    }
