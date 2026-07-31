"""Configuración de la capa de intención (experimento controlado).

Archivo de datos puro -- sin lógica, sin imports del motor de búsqueda. Ver
DISENO_MINIMO_CONCEPTOS.md para la investigación y validación detrás de cada
concepto, y ARQUITECTURA_INTENCION.md para el análisis de arquitectura
completo.

Alcance deliberado: solo los 5 conceptos que aparecieron en el QA de usuario
(EXPERIENCIA_USUARIO.md). Agregar un concepto nuevo es un registro más en
CONCEPTOS, con la misma forma que estos cinco -- no requiere tocar el motor
de búsqueda ni la lógica de detección en capa_intencion.py.
"""

# Palabras que describen DÓNDE o PARA QUÉ se usa un producto, no QUÉ es.
# Compartidas por los 5 conceptos: si un concepto se activa, estas palabras
# se ignoran como requisito de coincidencia en vez de forzar un AND que casi
# nunca se cumple (ej. "sala" aparece en 3 de 30,550 productos del catálogo).
PALABRAS_CONTEXTO = (
    "sala", "cocina", "baño", "cuarto", "dormitorio", "comedor",
    "casa", "apartamento", "cerca", "perimetral", "jardín", "patio",
    "interior", "exterior", "pared", "paredes",
)

CONCEPTOS = (
    {
        "nombre": "piso_ceramico",
        "disparadores": ("azulejo", "azulejos", "baldosa", "baldosas"),
        "categorias_permitidas": ("Pisos",),
        "exclusiones": (),
        "contexto_requerido": (),
    },
    {
        "nombre": "bloque_construccion",
        "disparadores": ("block", "bloque", "bloques"),
        "categorias_permitidas": ("Construcción", "Construccion"),
        "exclusiones": (),
        "contexto_requerido": (),
    },
    {
        "nombre": "cemento_construccion",
        "disparadores": ("cemento",),
        "categorias_permitidas": ("Construcción", "Construccion"),
        "exclusiones": (),
        "contexto_requerido": (),
    },
    {
        "nombre": "malla_cerca",
        "disparadores": ("malla",),
        "categorias_permitidas": ("Construcción", "Construccion"),
        "exclusiones": (),
        # Único de los 5 que necesita contexto para activarse: "malla" sola
        # es demasiado ambigua incluso dentro del catálogo (mosquiteros,
        # malla industrial inoxidable, y malla de cerca conviven bajo la
        # misma palabra) -- ver DISENO_MINIMO_CONCEPTOS.md.
        "contexto_requerido": ("cerca", "perimetral", "jardín", "patio"),
    },
    {
        "nombre": "pintura_pared",
        "disparadores": ("pintura",),
        "categorias_permitidas": ("Pinturas",),
        "exclusiones": ("sellador",),
        "contexto_requerido": (),
    },
)
