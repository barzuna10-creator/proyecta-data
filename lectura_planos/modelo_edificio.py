"""LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO -- primer modelo estructurado del
edificio, construido únicamente con información explícita ya presente en
los planos (ver LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO.md para la auditoría
completa que fundamenta qué se modela y qué se dejó fuera).

Registrado sobre el mismo registro extensible de extractores.py que
LECTURA_DE_PLANOS_V1_MVP.md dejó preparado -- cero cambios en nucleo.py.

Deliberadamente NO incluye acabados-por-espacio ni puertas/ventanas
asociadas a un espacio: la auditoría de esta fase midió, contra el nivel
0.0 m del plano real, que esas relaciones no se pueden resolver con texto
inequívoco -- solo 12/18 (67%) códigos de cielo y 5/12 (42%) marcadores de
puerta/ventana tienen un único ambiente candidato claramente más cercano
que el segundo. Resolver el resto exigiría calcular distancias o probar
contención de polígono -- ambas explícitamente excluidas ("no quiero medir
geometría", "no quiero inferencias"). Se detuvo esa parte del modelo en
vez de forzar una relación que no está garantizada por el texto.

Lo que sí tiene evidencia explícita e inequívoca:

- Niveles: se derivan del propio nombre de cada lámina (ej. "... N 0.0 M"),
  ya extraído por el núcleo -- ninguna lectura nueva del PDF.
- Espacios: nombres de ambiente impresos como texto en las láminas
  "PLANTA DE DISTRIBUCION ARQUITECTONICA" -- un catálogo, no una relación
  con otro dato, así que no hay ambigüedad que resolver.
- Referencias entre láminas: los callouts de sección ("A" -> "A301") y de
  detalle ("DETALLE 1" -> "A804") vienen en un solo bloque de texto que ya
  incluye su propio destino -- se lee tal cual, no se empareja con nada.
"""

import re

from .extractores import ContextoLectura, ResultadoExtractor, registrar_lamina
from .modelo import Espacio, ModeloEdificio, Nivel, ReferenciaLamina

PATRON_NIVEL_EN_NOMBRE = re.compile(r"N\s+([+-]?\d+(?:\.\d+)?)\s*M\b", re.IGNORECASE)
PATRON_TITULO_DISTRIBUCION = "PLANTA DE DISTRIBUCION ARQUITECTONICA"

PATRON_SECCION = re.compile(r"^([A-H])\n(A\d{3})$")
PATRON_DETALLE = re.compile(r"^DETALLE\n(\d+)\n([AS]\d{3})$")

# Palabras que descartan un bloque de texto como candidato a nombre de
# espacio -- todas confirmadas contra ruido real encontrado en la
# auditoría (cotas, notas de acabado, elementos de jardín, cajetín).
PALABRAS_EXCLUIDAS_ESPACIO = (
    "NPT", "NCT", "DETALLE", "JARDINERA", "BARANDA", "PROYECCION", "SISA",
    "MACETERA", "REJILLA", "ACCESO EN", "PIEL EN", "BANCA", "CENEFA",
    "FECHA", "CONTENIDO", "INFORMACION", "PROPIETARIO", "CATASTRO", "CITAS",
    "VISTAS DE DESCANSO", "PLANO ORIGINAL", "TABLA", "ESCALA", "ACOTADO",
    "BAÑO", "REGLAMENTO", "N° PROYECTO",
)


def _parece_nombre_espacio(texto):
    t = texto.strip()
    if not t or len(t) < 3 or re.search(r"\d", t):
        return False
    if any(p in t.upper() for p in PALABRAS_EXCLUIDAS_ESPACIO):
        return False
    return True


# ---------------------------------------------------------------------------
# Espacios -- solo en la lámina canónica de distribución arquitectónica,
# nunca en cielos/acabados/puertas-ventanas (esas comparten los mismos
# nombres pero mezclados con su propia leyenda -- la de distribución es la
# única cuyo propósito explícito es nombrar los espacios).
#
# La identificación de "es esta la lámina de distribución" se hace por
# CONTEO exacto de coincidencias del título dentro del área de dibujo, no
# por búsqueda simple: la hoja de índice (portada) menciona el mismo
# título varias veces como texto de la tabla de contenidos -- si se
# buscara en toda la página, la portada calzaría por accidente y
# contaminaría el catálogo de espacios con notas generales y abreviaturas
# (caso real encontrado y corregido, ver
# LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO.md). Se exige además que el título
# aparezca como texto grande (bloque corto, no dentro de una fila de
# tabla) en la esquina inferior donde vive el título de toda lámina.
# ---------------------------------------------------------------------------


def _es_lamina_de_distribucion(pagina):
    r = pagina.rect
    banda_titulo = (r.width * 0.5, r.height * 0.85, r.width, r.height)
    for b in pagina.get_text("blocks", clip=banda_titulo):
        if PATRON_TITULO_DISTRIBUCION in " ".join(b[4].split()).upper():
            return True
    return False


PATRON_RUIDO_LETRAS_GRILLA = re.compile(r"^[A-Z](\s+[A-Z])+$")
LONGITUD_MAXIMA_NOMBRE_ESPACIO = 40  # una nota de especificación real mide muchas veces más que esto


@registrar_lamina("espacios")
def extraer_espacios(contexto: ContextoLectura, numero_pagina: int) -> ResultadoExtractor:
    pagina = contexto.documento[numero_pagina]
    if not _es_lamina_de_distribucion(pagina):
        return ResultadoExtractor("espacios", {"nombres": []})

    r = pagina.rect
    x_limite_planta = r.width * 0.62  # excluye cajetín/leyenda del lado derecho
    espacios = []
    for b in pagina.get_text("blocks"):
        if b[0] >= x_limite_planta:
            continue
        texto = b[4].strip()
        texto_una_linea = " ".join(texto.split())
        if not _parece_nombre_espacio(texto):
            continue
        if PATRON_RUIDO_LETRAS_GRILLA.match(texto_una_linea):
            continue
        if len(texto_una_linea) > LONGITUD_MAXIMA_NOMBRE_ESPACIO:
            continue
        espacios.append((texto.replace("\n", " "), texto_una_linea))

    return ResultadoExtractor("espacios", {"nombres": espacios})


# ---------------------------------------------------------------------------
# Referencias entre láminas -- callouts de sección y de detalle, en
# cualquier hoja (no solo distribución).
# ---------------------------------------------------------------------------


@registrar_lamina("referencias_laminas")
def extraer_referencias_laminas(contexto: ContextoLectura, numero_pagina: int) -> ResultadoExtractor:
    pagina = contexto.documento[numero_pagina]
    vistos = set()
    referencias = []
    for b in pagina.get_text("blocks"):
        texto = b[4].strip()

        m = PATRON_SECCION.match(texto)
        if m:
            clave = ("seccion", m.group(1), m.group(2))
            if clave not in vistos:  # la misma marca de sección suele repetirse en ambos bordes de la hoja
                vistos.add(clave)
                referencias.append(("seccion", m.group(1), m.group(2)))
            continue

        m = PATRON_DETALLE.match(texto)
        if m:
            clave = ("detalle", m.group(1), m.group(2))
            if clave not in vistos:
                vistos.add(clave)
                referencias.append(("detalle", m.group(1), m.group(2)))

    return ResultadoExtractor("referencias_laminas", {"filas": referencias})


# ---------------------------------------------------------------------------
# Construcción del modelo -- corre DESPUÉS de leer_proyecto(), igual que
# cuadros.agregar_cuadros(). No vuelve a abrir el PDF.
# ---------------------------------------------------------------------------


def _derivar_niveles(proyecto):
    por_elevacion = {}
    for lamina in proyecto.laminas:
        if not lamina.nombre:
            continue
        m = PATRON_NIVEL_EN_NOMBRE.search(lamina.nombre)
        if not m:
            continue
        texto_nivel = f"N {m.group(1)} M"
        elevacion = float(m.group(1))
        por_elevacion.setdefault((texto_nivel, elevacion), []).append(lamina.codigo)

    niveles = [
        Nivel(nombre=nombre, elevacion=elevacion, laminas=tuple(sorted(filter(None, laminas))))
        for (nombre, elevacion), laminas in por_elevacion.items()
    ]
    niveles.sort(key=lambda n: n.elevacion)
    return niveles


def _nivel_de_pagina(niveles, codigo_lamina):
    for nivel in niveles:
        if codigo_lamina in nivel.laminas:
            return nivel.nombre
    return None


def construir_modelo_edificio(proyecto) -> ModeloEdificio:
    advertencias = []

    niveles = _derivar_niveles(proyecto)
    if not niveles:
        advertencias.append("no se encontró ningún nombre de lámina con patrón de nivel (\"N ... M\").")

    espacios = []
    for lamina in proyecto.laminas:
        resultado = lamina.extras.get("espacios")
        if not resultado:
            continue
        nivel_nombre = _nivel_de_pagina(niveles, lamina.codigo)
        vistos_en_pagina = set()
        for nombre, texto_original in resultado.datos["nombres"]:
            if nombre in vistos_en_pagina:
                continue
            vistos_en_pagina.add(nombre)
            espacios.append(
                Espacio(nombre=nombre, nivel=nivel_nombre, pagina_fuente=lamina.numero_pagina, texto_original=texto_original)
            )

    codigos_indice = {e.codigo for e in proyecto.indice} if proyecto.indice else set()
    referencias = []
    for lamina in proyecto.laminas:
        resultado = lamina.extras.get("referencias_laminas")
        if not resultado:
            continue
        for tipo, identificador, destino in resultado.datos["filas"]:
            destino_existe = destino in codigos_indice if codigos_indice else True
            if not destino_existe:
                advertencias.append(
                    f"lámina {lamina.codigo} (página {lamina.numero_pagina}): la referencia de {tipo} "
                    f"'{identificador}' apunta a '{destino}', que no aparece en el índice de este documento."
                )
            referencias.append(
                ReferenciaLamina(
                    tipo=tipo,
                    identificador=identificador,
                    lamina_origen=lamina.codigo,
                    lamina_destino=destino,
                    pagina_fuente=lamina.numero_pagina,
                    destino_existe=destino_existe,
                )
            )

    advertencias.append(
        "acabados-por-espacio y puertas/ventanas-asociadas NO se incluyen en este modelo: "
        "la auditoría de LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO.md midió que esas relaciones no tienen "
        "un único candidato inequívoco por texto explícito (67% para acabados de cielo, 42% para "
        "puertas/ventanas, medido en el nivel 0.0 m) -- resolver el resto exigiría distancia geométrica "
        "o contención de polígono, ambas fuera de alcance de esta fase."
    )

    return ModeloEdificio(
        proyecto_nombre=proyecto.nombre,
        niveles=niveles,
        espacios=espacios,
        referencias_laminas=referencias,
        advertencias=advertencias,
    )
