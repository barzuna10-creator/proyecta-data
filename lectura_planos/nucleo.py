"""Orquestador de LECTURA_DE_PLANOS_V1_MVP: leer_proyecto(ruta_pdf) -> Proyecto.

Corre la clasificación de tipo de PDF por hoja y después TODOS los
extractores registrados (documento y lámina) de forma genérica -- agregar
un extractor nuevo en extractores.py nunca requiere tocar este archivo,
salvo que ese extractor deba alimentar un campo canónico del modelo
(código/nombre/disciplina), que es exactamente lo que índice y cajetín ya
hacen en este MVP. Ver LECTURA_DE_PLANOS_V1_MVP.md, sección de diseño.
"""

import re
from pathlib import Path

import fitz

from .clasificacion import clasificar_disciplina, clasificar_pagina
from .extractores import (
    ContextoLectura,
    extractores_documento_registrados,
    extractores_lamina_registrados,
)
from .modelo import Lamina, Proyecto

ETIQUETAS_CONOCIDAS_CAJETIN = {
    "PROYECTO", "PROPIETARIO", "UBICACION", "UBICACIÓN", "FECHA", "PÁGINA",
    "PAGINA", "DP", "IP", "ESCALA", "LAMINA", "LÁMINA", "CONTENIDO",
    "CONTENIDO DE LAMINA", "CITAS", "CATASTRO",
}
PATRON_ETIQUETA_PROYECTO = re.compile(r"^PROYECTO\s*:\s*$", re.IGNORECASE)
PAGINAS_MAX_BUSQUEDA_NOMBRE_PROYECTO = 3


def _parece_etiqueta(linea: str) -> bool:
    limpio = linea.strip().rstrip(":").upper()
    return limpio in ETIQUETAS_CONOCIDAS_CAJETIN


def _extraer_nombre_proyecto(documento) -> str | None:
    """Busca una línea "PROYECTO:" en las primeras hojas y toma la línea
    siguiente como nombre -- solo si esa línea no es, a su vez, otra
    etiqueta conocida (evita devolver "Ubicación:" cuando el campo real
    está vacío en el PDF, como pasa en uno de los dos planos auditados)."""
    for numero in range(min(PAGINAS_MAX_BUSQUEDA_NOMBRE_PROYECTO, documento.page_count)):
        lineas = [linea.strip() for linea in documento[numero].get_text("text").splitlines()]
        for i, linea in enumerate(lineas):
            if PATRON_ETIQUETA_PROYECTO.match(linea) and i + 1 < len(lineas):
                candidata = lineas[i + 1]
                if candidata and not _parece_etiqueta(candidata):
                    return candidata
    return None


def _normalizar(texto: str) -> str:
    return " ".join(texto.upper().split())


def leer_proyecto(ruta_pdf: str) -> Proyecto:
    documento = fitz.open(ruta_pdf)
    advertencias = []

    tipos_por_pagina = [clasificar_pagina(documento[i]) for i in range(documento.page_count)]
    contexto = ContextoLectura(documento=documento, ruta_pdf=ruta_pdf, tipos_por_pagina=tipos_por_pagina)

    # -- Extractores de documento (índice, y cualquier extractor futuro que se registre) --
    resultados_documento = {}
    for nombre, extractor in extractores_documento_registrados().items():
        resultado = extractor(contexto)
        resultados_documento[nombre] = resultado
        advertencias.extend(f"[{nombre}] {a}" for a in resultado.advertencias)

    resultado_indice = resultados_documento.get("indice")
    entradas_indice = resultado_indice.datos.get("entradas") if resultado_indice else None
    indice_por_codigo = {e.codigo: e for e in entradas_indice} if entradas_indice else {}

    # -- Extractores de lámina (cajetín, y cualquier extractor futuro que se registre) --
    laminas = []
    for numero in range(documento.page_count):
        resultados_lamina = {}
        for nombre, extractor in extractores_lamina_registrados().items():
            resultado = extractor(contexto, numero)
            resultados_lamina[nombre] = resultado
            advertencias.extend(f"hoja {numero + 1} [{nombre}]: {a}" for a in resultado.advertencias)

        resultado_cajetin = resultados_lamina.get("cajetin")
        datos_cajetin = resultado_cajetin.datos if resultado_cajetin else {}
        codigo = datos_cajetin.get("codigo")
        nombre_lamina = datos_cajetin.get("nombre")

        entrada_indice = indice_por_codigo.get(codigo) if codigo else None
        if entrada_indice and not nombre_lamina:
            nombre_lamina = entrada_indice.nombre
        elif entrada_indice and nombre_lamina and _normalizar(nombre_lamina) != _normalizar(entrada_indice.nombre):
            advertencias.append(
                f"hoja {numero + 1} ({codigo}): el nombre leído del cajetín "
                f"({nombre_lamina!r}) no coincide con el del índice ({entrada_indice.nombre!r})."
            )

        texto_para_disciplina = " ".join(filter(None, [nombre_lamina, datos_cajetin.get("texto_crudo", "")]))
        disciplina = clasificar_disciplina(texto_para_disciplina) if texto_para_disciplina.strip() else None

        extras_lamina = {n: r for n, r in resultados_lamina.items() if n != "cajetin"}

        laminas.append(
            Lamina(
                numero_pagina=numero + 1,
                tipo_pdf=tipos_por_pagina[numero],
                codigo=codigo,
                nombre=nombre_lamina,
                disciplina=disciplina,
                cajetin=datos_cajetin,
                extras=extras_lamina,
            )
        )

    if entradas_indice:
        codigos_encontrados = {l.codigo for l in laminas if l.codigo}
        for entrada in entradas_indice:
            if entrada.codigo not in codigos_encontrados:
                advertencias.append(
                    f"el índice menciona la lámina {entrada.codigo} ({entrada.nombre}) "
                    "pero no se encontró una hoja con ese código en el PDF."
                )

    disciplinas_encontradas = {l.disciplina for l in laminas if l.disciplina}
    if len(disciplinas_encontradas) == 1:
        disciplina_proyecto = next(iter(disciplinas_encontradas))
    elif disciplinas_encontradas:
        disciplina_proyecto = "mixto"
    else:
        disciplina_proyecto = "sin_determinar"

    nombre_proyecto = _extraer_nombre_proyecto(documento)
    if not nombre_proyecto:
        nombre_proyecto = Path(ruta_pdf).stem
        advertencias.append("no se encontró el nombre del proyecto en el PDF -- se usó el nombre del archivo.")

    extras_documento = {n: r for n, r in resultados_documento.items() if n != "indice"}

    documento.close()

    return Proyecto(
        ruta_pdf=ruta_pdf,
        nombre=nombre_proyecto,
        cantidad_laminas=len(laminas),
        disciplina=disciplina_proyecto,
        laminas=laminas,
        indice=entradas_indice,
        advertencias=advertencias,
        extras=extras_documento,
    )
