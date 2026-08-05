"""Clasificación de tipo de PDF y de disciplina (etapas [0]/[1] de
LECTURA_DE_PLANOS_V1_ARQUITECTURA.md).

Advertencia de confiabilidad (ver LECTURA_DE_PLANOS_V1_MVP.md, sección de
limitaciones): VECTORIAL_CON_TEXTO está validado contra los dos juegos de
planos reales de la auditoría (77/77 hojas se clasificaron así). Las otras
tres categorías de TipoPdf (VECTORIAL_SIN_TEXTO, HIBRIDO, ESCANEADO) son
heurísticas razonadas a partir de LECTURA_DE_PLANOS_V1_ARQUITECTURA.md,
sección 2 -- ninguna hoja de los planos disponibles cayó en esos casos, así
que sus umbrales NO están medidos contra un ejemplo real todavía.
"""

from .modelo import TipoPdf

UMBRAL_TEXTO_MINIMO = 20  # caracteres -- por debajo, se considera que la hoja no tiene texto real
UMBRAL_DIBUJOS_MINIMO = 5  # trazos vectoriales -- por debajo, se considera que no hay geometría real
UMBRAL_DIBUJOS_SOSPECHOSO = 1000  # sin texto real pero con más dibujos que esto: probable texto aplanado a curvas (híbrido)


def clasificar_pagina(pagina) -> TipoPdf:
    """Clasifica una sola hoja (objeto Page de PyMuPDF)."""
    texto = pagina.get_text("text").strip()
    num_dibujos = len(pagina.get_drawings())
    num_imagenes = len(pagina.get_images())

    tiene_texto = len(texto) >= UMBRAL_TEXTO_MINIMO
    tiene_geometria = num_dibujos >= UMBRAL_DIBUJOS_MINIMO

    if tiene_texto and tiene_geometria:
        return TipoPdf.VECTORIAL_CON_TEXTO
    if not tiene_texto and num_dibujos >= UMBRAL_DIBUJOS_SOSPECHOSO:
        return TipoPdf.HIBRIDO
    if not tiene_texto and not tiene_geometria and num_imagenes > 0:
        return TipoPdf.ESCANEADO
    return TipoPdf.VECTORIAL_SIN_TEXTO


# Disciplinas tomadas de LECTURA_DE_PLANOS_V1_ARQUITECTURA.md, sección 1 (tabla
# de tipos de plano). Coincidencia por palabra clave, determinística -- sin IA.
# El orden importa: se evalúa en este orden y gana la primera coincidencia,
# así que las categorías más específicas van antes que las más generales.
PALABRAS_CLAVE_DISCIPLINA = {
    "indice": ("INDICE DE PLANOS", "ÍNDICE DE PLANOS", "INDICE DE LAMINAS", "PORTADA"),
    "puertas_ventanas": ("PUERTAS Y VENTANAS", "DE VENTANAS", "DE PUERTAS"),
    "acabados": ("ACABADO",),
    "electrico": ("ELECTRIC", "LUMINARIA", "TOMACORRIENTE", "CIRCUITO", "TABLERO ELECTRICO"),
    "hidrosanitario": ("HIDROSANITAR", "SANITARIO", "PLOMERIA", "TUBERIA", "AGUAS RESIDUALES"),
    "estructural": (
        "ESTRUCTURA", "ESTRUCTURAL", "VIGA", "COLUMNA", "CORONA", "ENTREPISO",
        "CIMENTACION", "REFUERZO", "PLANTA ESTRUCTURAL",
    ),
    "techos": ("CUBIERTA", "TECHO", "CIELO"),
    "sitio": ("PLANTA DE CONJUNTO", "LOCALIZACION", "MOVIMIENTO DE TIERRA", "SECCIONES DE TERRENO"),
    "detalle": ("DETALLE",),
    "arquitectonico": ("ARQUITECTON",),
}


def clasificar_disciplina(texto: str) -> str | None:
    """Determina la disciplina a partir de texto real (nombre de lámina o
    contenido del cajetín). Devuelve None -- nunca una adivinanza -- cuando
    ninguna palabra clave conocida aparece."""
    texto_norm = texto.upper()
    for disciplina, palabras in PALABRAS_CLAVE_DISCIPLINA.items():
        if any(palabra in texto_norm for palabra in palabras):
            return disciplina
    return None
