"""EXTRACTOR_COMPUTO_ESTRUCTURAL_V1 -- extractor del cómputo de piezas de
madera ya impreso en el plano estructural (ver
EXTRACTOR_COMPUTO_ESTRUCTURAL_V1.md para la auditoría completa que
fundamenta cada umbral de este archivo).

Registrado como extractor de lámina (@registrar_lamina) sobre el mismo
registro extensible de extractores.py que LECTURA_DE_PLANOS_V1_MVP.md
dejó preparado -- cero cambios en nucleo.py.

Un solo cuadro real, medido contra el único plano estructural
disponible (Atelier Ingeniería, "Detalle de vigas y columnas"): 99
líneas / 11 piezas únicas, repetidas idénticas en 9 de 19 hojas.
Confirmado con evidencia, no supuesto, ANTES de escribir este archivo
(ver el .md): esta técnica está calibrada contra una sola firma -- no
hay una segunda muestra de plano estructural para confirmar que el
título, el patrón de texto o la posición relativa generalizan. Si un
futuro plano trae un cómputo con otro título u otro formato, se agrega
como un extractor nuevo y separado (mismo mecanismo de registro), nunca
se generaliza este regex a ciegas sin medir primero contra el caso real.

Diferencia clave frente a TABLA DE PUERTAS/TABLA DE VENTANAS
(LECTURA_DE_PLANOS_V2_CUADROS.md): aquella tabla siempre vive en la misma
posición relativa a la PÁGINA (fracción fija del ancho). Esta, medido,
NO -- el título aparece en distintos cuadrantes de la hoja según la
página (comprobado: dos hojas lo tienen arriba a la izquierda, el resto
a la derecha), pero su contenido está siempre a un offset FIJO respecto
al propio título (x0-58pt, y0+31pt, ~334x392pt de extensión, medido en
las 9 hojas). Por eso el recorte de esta extracción es relativo al
título, no a la página.
"""

import re

import fitz

from .extractores import ContextoLectura, ResultadoExtractor, registrar_lamina
from .modelo import PiezaEstructural

TITULO_CUADRO = "Detalle de vigas y columnas"

# Offset medido del título a la región de contenido -- ver docstring del
# módulo. Generoso a propósito (más margen que el mínimo medido) para
# tolerar variación menor de una hoja a otra sin perder texto.
OFFSET_X0 = -75
OFFSET_Y0 = 20
OFFSET_X1 = 400
OFFSET_Y1 = 450

PATRON_PIEZA = re.compile(
    r"^(\d+)\s+[Pp]iezas?\s+de\s+([\d.]+)\s*mm\s*x\s*([\d.]+)\s*mm\s*x?\s*([\d.]+)\s*mts?\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)


@registrar_lamina("computo_estructural")
def extraer_computo_estructural(contexto: ContextoLectura, numero_pagina: int) -> ResultadoExtractor:
    pagina = contexto.documento[numero_pagina]
    cajas = pagina.search_for(TITULO_CUADRO)
    if not cajas:
        return ResultadoExtractor("computo_estructural", {"filas": []})

    caja = cajas[0]
    r = pagina.rect
    clip = fitz.Rect(
        max(0, caja.x0 + OFFSET_X0),
        caja.y0 + OFFSET_Y0,
        min(r.width, caja.x0 + OFFSET_X1),
        min(r.height, caja.y0 + OFFSET_Y1),
    )

    advertencias = []
    filas = []
    for bloque in pagina.get_text("blocks", clip=clip):
        texto = bloque[4].strip()
        if "pieza" not in texto.lower():
            continue

        texto_una_linea = " ".join(texto.split())
        m = PATRON_PIEZA.match(texto_una_linea)
        if not m:
            advertencias.append(
                f"línea con la palabra 'pieza' que no calzó con el patrón esperado: {texto_una_linea!r}"
            )
            continue

        cantidad, ancho, alto, largo, descripcion = m.groups()
        filas.append(
            PiezaEstructural(
                cantidad=int(cantidad),
                ancho_mm=float(ancho),
                alto_mm=float(alto),
                largo_m=float(largo),
                descripcion=descripcion.strip(),
                pagina_fuente=numero_pagina + 1,
                texto_original=texto_una_linea,
                confianza="alta",
            )
        )

    return ResultadoExtractor("computo_estructural", {"filas": filas}, advertencias)


def agregar_computo_estructural(proyecto):
    """Recorre todas las láminas ya leídas (ver nucleo.leer_proyecto) y
    devuelve el cómputo de piezas deduplicado -- el mismo cuadro se repite
    idéntico en cada hoja de detalle (confirmado: 9/9 hojas byte-idénticas
    en el plano de referencia).

    La deduplicación es por HOJA COMPLETA (la tupla ordenada de todo su
    texto), no por línea individual: el cuadro real trae dos filas con
    texto idéntico ("1 Pieza de 195mm x 195mm x 2.65mts columna" aparece
    dos veces -- dos columnas físicas distintas, no un error de
    transcripción). Deduplicar por línea colapsaba esas dos piezas reales
    en una sola (bug real encontrado al validar contra el plano completo,
    ver EXTRACTOR_COMPUTO_ESTRUCTURAL_V1.md) -- deduplicar por hoja
    completa las conserva a ambas, y solo colapsa repeticiones genuinas
    del mismo cuadro entre hojas."""
    grupos = {}
    for lamina in proyecto.laminas:
        resultado = lamina.extras.get("computo_estructural")
        if not resultado or not resultado.datos["filas"]:
            continue
        piezas_hoja = resultado.datos["filas"]
        clave_hoja = tuple(p.texto_original for p in piezas_hoja)
        grupos.setdefault(clave_hoja, piezas_hoja)

    advertencias = []
    if len(grupos) > 1:
        advertencias.append(
            f"se encontraron {len(grupos)} versiones distintas del cómputo en páginas distintas -- "
            "se usa la primera encontrada; revisar manualmente cuál es la vigente."
        )

    piezas = next(iter(grupos.values()), [])
    return {
        "piezas": piezas,
        "cantidad_total_piezas": sum(p.cantidad for p in piezas),
        "advertencias": advertencias,
    }
