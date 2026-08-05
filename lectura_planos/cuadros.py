"""LECTURA_DE_PLANOS_V2_CUADROS -- extractores de cuadros reales de
acabados, puertas y ventanas (ver LECTURA_DE_PLANOS_V2_CUADROS.md para la
auditoría completa que fundamenta cada técnica y umbral de este archivo).

Registrados como extractores de lámina (@registrar_lamina) sobre el
registro extensible de extractores.py -- exactamente el mecanismo que
LECTURA_DE_PLANOS_V1_MVP.md prometía para fases futuras: este archivo no
modifica nucleo.py, modelo.py (salvo agregar los 3 dataclasses nuevos,
aditivo) ni extractores.py.

Dos técnicas de extracción medidas, no una regla universal:

1. TABLA DE ACABADOS DE MUROS Y PAREDES: `pdfplumber.extract_tables()`
   agrupa esta tabla en un solo bloque limpio -- confianza alta.
2. TABLA DE ACABADOS DE PISOS/CIELOS: la misma llamada FRAGMENTA esta
   tabla en decenas de fragmentos de 1-2 filas (medido, no es un fallback
   automático "por si acaso") -- se usa reconstrucción por posición de
   palabras, anclada en los encabezados de columna reales (nunca en el
   título, que puede compartir palabras con un encabezado) y en la
   posición de cada código como límite de fila. Confianza media: se
   comprobó al menos un caso real donde una palabra al borde de una
   columna se pierde de la celda estructurada (queda en texto_original).
3. TABLA DE PUERTAS / TABLA DE VENTANAS: nunca se agrupan como tabla real
   con extract_tables() (title/código/descripción son bloques de texto
   sueltos, no una grilla con líneas). Reconstrucción por posición de
   bloques de texto (PyMuPDF get_text("blocks")): cada código define el
   inicio de su fila, cada bloque que empieza con la palabra
   PUERTA/VENTANA es una descripción candidata. Cuando dos descripciones
   quedan fusionadas en un solo bloque (sin espacio vertical suficiente
   entre ellas en el PDF -- caso real encontrado y corregido) se separan
   por la propia palabra clave repetida dentro del bloque.

Nunca se corre extract_tables() a ciegas sobre una hoja completa: todo
parte de localizar el título exacto de un cuadro conocido con
`page.search_for()` antes de tocar cualquier tabla o bloque de texto.
"""

import re

import fitz
import pdfplumber

from .extractores import ContextoLectura, ResultadoExtractor, registrar_lamina
from .modelo import CuadroAcabados, CuadroPuertas, CuadroVentanas

PATRON_CODIGO_PV = re.compile(r"^([A-Z]\d{1,2})\s*\n\s*([\d.]+)$")
PATRON_CODIGO_ACABADO = re.compile(r"^([A-Z]?\d{1,2})$")
PATRON_BUQUE = re.compile(r"BUQUE\s+DE\s+(.+?)\s*[xX]\s*(.+?)\s*m\.?", re.IGNORECASE)
PATRON_NUMERO_SIMPLE = re.compile(r"^\d+(?:\.\d+)?$")
PATRON_COLOR = re.compile(r"COLOR\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ ]{2,30})", re.IGNORECASE)

TIPOS_PUERTA_VENTANA = (
    "PIVOTANTE", "ABATIBLE", "CORREDIZA ESQUINERA", "CORREDIZA", "FIJA", "VENTILA",
)
MATERIALES_PUERTA_VENTANA = (
    "MADERA", "VIDRIO", "LAMINA DE HN", "MDF", "ALUMINIO",
)

ENCABEZADOS_ACABADOS = ("CODIGO", "ACABADO", "MARCA", "MODELO", "ESPECIFICACIONES", "DISTRIBUIDOR", "OBSERVACIONES")


# ---------------------------------------------------------------------------
# Utilidades de parseo -- deterministas, nunca infieren un valor ausente
# ---------------------------------------------------------------------------


def _parsear_medida_simple(texto):
    """Solo devuelve un número si el texto es un valor único y limpio --
    "ANCHO VARIABLE (0.95 - 1.07)" se deja como None (ver texto_original),
    nunca se inventa un promedio ni se toma el mínimo/máximo."""
    texto = texto.strip()
    if PATRON_NUMERO_SIMPLE.match(texto):
        return float(texto)
    return None


def _extraer_tipo(descripcion, prefijo):
    resto = descripcion.upper().split(prefijo, 1)[-1] if prefijo in descripcion.upper() else descripcion.upper()
    for tipo in TIPOS_PUERTA_VENTANA:
        if tipo in resto:
            return tipo.lower()
    return None


def _extraer_material(descripcion):
    descripcion_norm = descripcion.upper()
    for material in MATERIALES_PUERTA_VENTANA:
        if material in descripcion_norm:
            return material.lower()
    return None


def _extraer_color(texto):
    m = PATRON_COLOR.search(texto)
    return m.group(1).strip().title() if m else None


# ---------------------------------------------------------------------------
# Puertas / Ventanas
# ---------------------------------------------------------------------------


def _dividir_bloque_descripcion(texto, palabra):
    partes = re.split(rf"(?=\b{palabra}\b)", texto)
    return [p.strip() for p in partes if p.strip()]


def _extraer_cuadro_pv(pagina, numero_pagina, titulo, letra, palabra, constructor):
    cajas = pagina.search_for(titulo)
    if not cajas:
        return [], []

    r = pagina.rect
    clip = fitz.Rect(r.width * 0.55, cajas[0].y0 - 10, r.width, r.height)
    bloques = pagina.get_text("blocks", clip=clip)

    codigos, descripciones = [], []
    for b in bloques:
        y0, texto = b[1], b[4].strip()
        m = PATRON_CODIGO_PV.match(texto)
        if m and m.group(1).startswith(letra):
            codigos.append((y0, m.group(1)))
        elif texto.upper().startswith(palabra) and len(texto) > 15:
            for pedazo in _dividir_bloque_descripcion(texto, palabra):
                descripciones.append((y0, pedazo.replace("\n", " ")))

    if not codigos:
        return [], []

    codigos.sort(key=lambda c: c[0])
    descripciones.sort(key=lambda d: d[0])

    advertencias = []
    pares = []
    if len(codigos) == len(descripciones):
        pares = [(codigo, desc, "alta") for (_, codigo), (_, desc) in zip(codigos, descripciones)]
    else:
        advertencias.append(
            f"{len(codigos)} códigos vs {len(descripciones)} descripciones -- "
            "no calzan 1 a 1, se empareja por distancia vertical con confianza baja."
        )
        disponibles = list(descripciones)
        for y_cod, codigo in codigos:
            if not disponibles:
                pares.append((codigo, None, "baja"))
                continue
            mejor = min(disponibles, key=lambda d: abs(d[0] - y_cod))
            disponibles.remove(mejor)
            pares.append((codigo, mejor[1], "baja"))

    filas = []
    for codigo, descripcion, confianza in pares:
        ancho = alto = tipo = material = None
        if descripcion:
            m = PATRON_BUQUE.search(descripcion)
            if m:
                ancho = _parsear_medida_simple(m.group(1))
                alto = _parsear_medida_simple(m.group(2))
            tipo = _extraer_tipo(descripcion, palabra)
            material = _extraer_material(descripcion)
        filas.append(
            constructor(
                codigo=codigo,
                ancho=ancho,
                alto=alto,
                tipo=tipo,
                material=material,
                cantidad=None,  # ningún cuadro auditado trae columna de cantidad -- nunca se infiere
                pagina_fuente=numero_pagina,
                texto_original=descripcion or "",
                confianza=confianza,
            )
        )
    return filas, advertencias


@registrar_lamina("cuadro_puertas")
def extraer_cuadro_puertas(contexto: ContextoLectura, numero_pagina: int) -> ResultadoExtractor:
    pagina = contexto.documento[numero_pagina]
    filas, advertencias = _extraer_cuadro_pv(pagina, numero_pagina + 1, "TABLA DE PUERTAS", "P", "PUERTA", CuadroPuertas)
    return ResultadoExtractor("cuadro_puertas", {"filas": filas}, advertencias)


@registrar_lamina("cuadro_ventanas")
def extraer_cuadro_ventanas(contexto: ContextoLectura, numero_pagina: int) -> ResultadoExtractor:
    pagina = contexto.documento[numero_pagina]
    filas, advertencias = _extraer_cuadro_pv(pagina, numero_pagina + 1, "TABLA DE VENTANAS", "V", "VENTANA", CuadroVentanas)
    return ResultadoExtractor("cuadro_ventanas", {"filas": filas}, advertencias)


# ---------------------------------------------------------------------------
# Acabados (tres sub-cuadros posibles por hoja: muros_y_paredes, pisos, cielos)
# ---------------------------------------------------------------------------

TITULOS_ACABADOS = {
    "TABLA DE ACABADOS DE MUROS Y PAREDES": "muros_y_paredes",
    "TABLA DE ACABADOS DE PISOS": "pisos",
    "TABLA DE ACABADOS DE CIELOS": "cielos",
}


def _extraer_por_lineas(ruta_pdf, indice_pagina, titulo, caja_titulo, ancho_pagina, alto_pagina):
    """Técnica 1 -- confirmada limpia solo para MUROS Y PAREDES."""
    clip = (ancho_pagina * 0.5, caja_titulo.y0 - 10, ancho_pagina, alto_pagina)
    with pdfplumber.open(ruta_pdf) as pdf:
        tablas = pdf.pages[indice_pagina].crop(clip).extract_tables()
    for t in tablas:
        for fila in t[:3]:
            celdas = [c.upper() if c else "" for c in fila]
            if any("CODIGO" in c for c in celdas):
                return t[t.index(fila) :]
    return None


def _extraer_por_palabras(pagina, titulo, caja_titulo):
    """Técnica 2 -- para PISOS/CIELOS, donde extract_tables() fragmenta."""
    r = pagina.rect
    region = fitz.Rect(r.width * 0.5, caja_titulo.y0 - 5, r.width, r.height)
    banda_encabezado = fitz.Rect(region.x0, caja_titulo.y1 + 1, region.x1, caja_titulo.y1 + 45)

    columnas = []
    for encabezado in ENCABEZADOS_ACABADOS:
        cajas = pagina.search_for(encabezado, clip=banda_encabezado)
        if cajas:
            columnas.append((cajas[0].x0, encabezado))
    columnas.sort()
    if not columnas or columnas[0][1] != "CODIGO":
        return None

    x_codigo_ini = columnas[0][0]
    x_codigo_fin = columnas[1][0] if len(columnas) > 1 else region.x1
    y_fin_encabezado = pagina.search_for("CODIGO", clip=banda_encabezado)[0].y1

    palabras = pagina.get_text("words", clip=fitz.Rect(region.x0, y_fin_encabezado, region.x1, region.y1))
    palabras.sort(key=lambda p: (p[1], p[0]))

    candidatos = sorted(
        (p[1], p[4]) for p in palabras
        if x_codigo_ini - 10 <= p[0] < x_codigo_fin - 5 and PATRON_CODIGO_ACABADO.match(p[4])
    )
    if not candidatos:
        return None

    # Corta la secuencia en el primer salto vertical anormal -- un texto
    # legal más abajo en la hoja (ej. "...ARTICULO 8 DEL REGLAMENTO...")
    # puede tener un dígito suelto que calza con el patrón de código por
    # coincidencia; nunca está a una distancia de fila razonable del
    # último código real (caso encontrado y corregido, ver
    # LECTURA_DE_PLANOS_V2_CUADROS.md).
    SALTO_MAXIMO_ENTRE_FILAS = 250
    codigos = [candidatos[0]]
    for actual in candidatos[1:]:
        if actual[0] - codigos[-1][0] > SALTO_MAXIMO_ENTRE_FILAS:
            break
        codigos.append(actual)

    limites_x = [c[0] for c in columnas] + [region.x1]
    encabezados = [c[1] for c in columnas]
    # Altura de la última fila proporcional a las filas ya vistas (más
    # ajustado que un margen fijo) -- evita que texto legal/cajetín varias
    # líneas más abajo se cuele como si fuera parte de la última fila real.
    gaps = [b[0] - a[0] for a, b in zip(codigos, codigos[1:])]
    altura_fila_tipica = (sorted(gaps)[len(gaps) // 2] if gaps else SALTO_MAXIMO_ENTRE_FILAS)
    y_fin_ultima_fila = min(codigos[-1][0] + altura_fila_tipica * 1.5, region.y1)
    filas = []
    for i, (y_codigo, codigo) in enumerate(codigos):
        y_ini = y_codigo - 3
        y_fin = codigos[i + 1][0] - 3 if i + 1 < len(codigos) else y_fin_ultima_fila
        celdas = ["" for _ in columnas]
        palabras_fila_cruda = []
        for p in palabras:
            if not (y_ini <= p[1] < y_fin):
                continue
            x0, texto = p[0], p[4]
            palabras_fila_cruda.append(texto)
            if x_codigo_ini - 10 <= x0 < x_codigo_fin - 5:
                continue  # columna del propio código, ya está en `codigo`
            for j in range(1, len(columnas)):
                if limites_x[j] - 35 <= x0 < limites_x[j + 1] - 5:
                    celdas[j] = (celdas[j] + " " + texto).strip()
                    break
        celdas[0] = codigo
        filas.append((encabezados, celdas, " ".join(palabras_fila_cruda)))
    return filas


PALABRAS_TEXTO_LEGAL = ("REGLAMENTO", "PROPIEDAD INTELECTUAL", "CONSENTIMIENTO", "REPRODUCCION", "C.F.I.A")


def _parece_texto_legal(texto_crudo):
    """Filtro de seguridad, no una heurística de negocio: si una fila
    "extraída" resulta ser en realidad el párrafo de derechos de autor del
    plano (puede pasar cuando un dígito suelto de ese párrafo cae, por
    coincidencia de posición, dentro de la columna CODIGO -- caso real
    encontrado en TABLA DE ACABADOS DE CIELOS), se descarta en vez de
    devolver una fila con datos inventados o basura."""
    texto_norm = texto_crudo.upper()
    return any(palabra in texto_norm for palabra in PALABRAS_TEXTO_LEGAL)


def _fila_a_cuadro_acabados(encabezados, celdas, texto_crudo, ubicacion, numero_pagina, confianza):
    valores = dict(zip(encabezados, celdas))
    return CuadroAcabados(
        codigo=valores.get("CODIGO", "").strip(),
        descripcion=valores.get("ACABADO") or None,
        marca=valores.get("MARCA") or None,
        formato=valores.get("MODELO") or None,
        color=_extraer_color(valores.get("ESPECIFICACIONES", "") or ""),
        ubicacion=ubicacion,
        pagina_fuente=numero_pagina,
        texto_original=texto_crudo,
        confianza=confianza,
    )


@registrar_lamina("cuadro_acabados")
def extraer_cuadro_acabados(contexto: ContextoLectura, numero_pagina: int) -> ResultadoExtractor:
    pagina = contexto.documento[numero_pagina]
    r = pagina.rect
    filas_totales = []
    advertencias = []

    for titulo, ubicacion in TITULOS_ACABADOS.items():
        cajas = pagina.search_for(titulo)
        if not cajas:
            continue
        caja_titulo = cajas[0]

        tabla = _extraer_por_lineas(contexto.ruta_pdf, numero_pagina, titulo, caja_titulo, r.width, r.height)
        if tabla and len(tabla) > 1:
            encabezados = [c.upper() if c else "" for c in tabla[0]]
            for fila in tabla[1:]:
                if not any(fila):
                    continue
                texto_crudo = " | ".join(c for c in fila if c)
                filas_totales.append(
                    _fila_a_cuadro_acabados(encabezados, fila, texto_crudo, ubicacion, numero_pagina + 1, "alta")
                )
            continue

        resultado_palabras = _extraer_por_palabras(pagina, titulo, caja_titulo)
        if resultado_palabras:
            for encabezados, celdas, texto_crudo in resultado_palabras:
                if _parece_texto_legal(texto_crudo):
                    advertencias.append(
                        f"'{titulo}': una fila candidata (código {celdas[0]!r}) coincidía con el pie legal "
                        "de la lámina, no con una fila real -- descartada."
                    )
                    continue
                filas_totales.append(
                    _fila_a_cuadro_acabados(encabezados, celdas, texto_crudo, ubicacion, numero_pagina + 1, "media")
                )
        else:
            advertencias.append(f"se encontró el título '{titulo}' pero no se pudo extraer ninguna fila.")

    return ResultadoExtractor("cuadro_acabados", {"filas": filas_totales}, advertencias)


# ---------------------------------------------------------------------------
# Agregación -- los tres cuadros se repiten idénticos en cada lámina de su
# grupo (confirmado: TABLA DE PUERTAS es byte-idéntica en A704/A705/A706,
# igual para VENTANAS y cada sub-cuadro de ACABADOS). agregar_cuadros()
# deduplica por código, y cuando dos apariciones del mismo código traen
# datos distintos -- típicamente porque una de las dos páginas tuvo un
# emparejamiento de confianza baja (ver _extraer_cuadro_pv) -- conserva la
# de MAYOR confianza, nunca "la primera que apareció" a ciegas (medido:
# una página de menor confianza puede aparecer antes en el documento que
# la correcta). Señala el desacuerdo siempre, aunque se resuelva solo.
# ---------------------------------------------------------------------------

_ORDEN_CONFIANZA = {"alta": 2, "media": 1, "baja": 0}


def _deduplicar(filas, clave):
    vistos = {}
    advertencias = []
    for fila in filas:
        k = clave(fila)
        anterior = vistos.get(k)
        if anterior is None:
            vistos[k] = fila
            continue
        if (anterior.texto_original or "") == (fila.texto_original or ""):
            continue  # duplicado exacto esperado (misma tabla repetida en otra lámina)

        mejor, otra = (
            (anterior, fila)
            if _ORDEN_CONFIANZA[anterior.confianza] >= _ORDEN_CONFIANZA[fila.confianza]
            else (fila, anterior)
        )
        vistos[k] = mejor
        advertencias.append(
            f"código {k}: aparece con datos distintos en la página {anterior.pagina_fuente} "
            f"(confianza {anterior.confianza}) y en la página {fila.pagina_fuente} (confianza {fila.confianza}) "
            f"-- se conserva la de página {mejor.pagina_fuente} (mayor confianza; descartada: {otra.texto_original!r})."
        )
    return list(vistos.values()), advertencias


def agregar_cuadros(proyecto):
    """Recorre todas las láminas ya leídas (ver nucleo.leer_proyecto) y
    devuelve los tres cuadros deduplicados. No vuelve a abrir el PDF --
    opera sobre lo que los extractores de lámina ya dejaron en
    Lamina.extras."""
    puertas, ventanas, acabados = [], [], []
    for lamina in proyecto.laminas:
        if "cuadro_puertas" in lamina.extras:
            puertas += lamina.extras["cuadro_puertas"].datos["filas"]
        if "cuadro_ventanas" in lamina.extras:
            ventanas += lamina.extras["cuadro_ventanas"].datos["filas"]
        if "cuadro_acabados" in lamina.extras:
            acabados += lamina.extras["cuadro_acabados"].datos["filas"]

    puertas_dedup, adv_p = _deduplicar(puertas, lambda f: f.codigo)
    ventanas_dedup, adv_v = _deduplicar(ventanas, lambda f: f.codigo)
    acabados_dedup, adv_a = _deduplicar(acabados, lambda f: (f.ubicacion, f.codigo))

    return {
        "puertas": puertas_dedup,
        "ventanas": ventanas_dedup,
        "acabados": acabados_dedup,
        "advertencias": adv_p + adv_v + adv_a,
    }
