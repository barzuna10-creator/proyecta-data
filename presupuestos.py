"""Presupuestos inteligentes -- MVP.

Objetivo: dado un proyecto, calcular cuánto costaría comprar cada renglón
en la alternativa más barata *solo quando se puede confirmar con
confianza suficiente que esa alternativa es realmente comparable*. Nunca
se inventa una relación ni se calcula un ahorro sobre algo que no está
confirmado -- ver ARQUITECTURA_PRESUPUESTOS_INTELIGENTES.md para el diseño
completo y PRESUPUESTOS_INTELIGENTES.md para la validación contra
productos reales que respalda las reglas de este archivo.

No toca FTS5, reranking.py, los crawlers, comparación ni la lógica base de
proyectos -- reutiliza similares.py (candidatos), especificaciones.py
(compatibilidad física) y repositorio_proyectos._obtener_items() (los
renglones ya con precio_actual/precio_al_agregar/disponible, patrón que ya
existía antes de este módulo).

Deliberadamente NO usa todavía: costo de envío, mínimo de compra,
programación dinámica, ILP ni heurísticas -- el diseño ya evaluó esas
estrategias y concluyó que, sin datos reales de costo de envío, el mínimo
por renglón (lo que este módulo hace) ya es la solución óptima, no una
aproximación.
"""

from especificaciones import (
    comparar_specs,
    extraer_presentacion_pintura,
    extraer_specs,
)
from similares import obtener_similares

CONFIRMADA = "equivalencia_confirmada"
PROBABLE = "equivalencia_probable"
NO_COMPARABLE = "no_comparable"

MINIMO_TOKENS_NOMBRE_SIN_MARCA = 2

# similares.py ya filtra unidades/números como "w", "pulg", "kg" de su
# propio candidateo (TOKENS_GENERICOS ahí), pero deja pasar palabras que
# describen la estructura de precio, no el producto -- "por", "kilo",
# "metro", "unidad". Sin este filtro, "Cemento Gris Por Kilo" y "Mortero
# Seco Por Kilo" comparten los tokens "por"/"kilo" y eso alcanzaba para
# "confirmar" el mortero como sustituto del cemento (caso real encontrado
# validando esto). Estas palabras no identifican el producto, así que no
# cuentan para el requisito de identidad de este archivo.
TOKENS_SIN_VALOR_IDENTIDAD = {
    "por", "kilo", "kilos", "libra", "libras", "unidad", "unidades",
    "metro", "metros", "litro", "litros", "saco", "sacos", "caja", "cajas",
}

MENSAJE_SIN_ALTERNATIVA_SEGURA = (
    "No se encontraron alternativas comparables con suficiente confianza"
)


def _tiene_razon(razones, prefijo):
    return any(r == prefijo or r.startswith(prefijo + ":") for r in razones)


def _tokens_nombre_de_razon(razones):
    for r in razones:
        if r.startswith("tokens_nombre:"):
            return set(r.split(":", 1)[1].split(","))
    return set()


def clasificar_equivalencia(objetivo, candidato):
    """Decide qué tan confiable es tratar `candidato` como sustituto de
    `objetivo` para efectos de comparar precio. `candidato` ya viene de
    similares.obtener_similares(depurar=True) -- ya pasó el umbral mínimo
    de esa función, así que "no calificó ni como candidato" no es un caso
    que este clasificador tenga que manejar; eso ya lo filtró similares.py.

    Devuelve (nivel, razon_legible, detalle)."""

    specs_objetivo = extraer_specs(objetivo["nombre"])
    specs_candidato = extraer_specs(candidato["nombre"])

    es_pintura = objetivo.get("categoria") == "Pinturas" or candidato.get("categoria") == "Pinturas"
    if es_pintura:
        pres_objetivo = extraer_presentacion_pintura(objetivo["nombre"])
        pres_candidato = extraer_presentacion_pintura(candidato["nombre"])
        if pres_objetivo and pres_candidato and pres_objetivo != pres_candidato:
            return (
                NO_COMPARABLE,
                f"Presentación distinta ({pres_objetivo} vs. {pres_candidato})",
                {"conflicto_en": ["presentacion"]},
            )

    comparacion = comparar_specs(specs_objetivo, specs_candidato)

    if comparacion["conflicto"]:
        campos = ", ".join(comparacion["conflicto_en"])
        return (
            NO_COMPARABLE,
            f"Especificación distinta ({campos})",
            comparacion,
        )

    razones = candidato.get("_razones", [])
    misma_familia = _tiene_razon(razones, "misma_familia")
    misma_subcategoria = _tiene_razon(razones, "misma_subcategoria")
    misma_marca = _tiene_razon(razones, "misma_marca")
    tokens_compartidos = _tokens_nombre_de_razon(razones) - TOKENS_SIN_VALOR_IDENTIDAD

    hay_asimetria_unidad_venta = bool(comparacion["asimetrias"])

    if misma_familia:
        return (CONFIRMADA, "Misma familia de producto", comparacion)

    # La marca nunca alcanza sola: un mismo proveedor/marca puede vender
    # "Cemento Gris Por Kilo" y "Mortero Seco Por Kilo" bajo la misma
    # subcategoría combinada ("Morteros, Cemento" en El Lagar) -- son
    # materiales distintos, no una alternativa. Confirmado con un caso real
    # durante la validación: sin exigir al menos 1 token real compartido,
    # el mortero se confirmaba como "sustituto" del cemento por compartir
    # marca y esa subcategoría combinada. Se exige token real siempre;
    # la marca solo baja cuántos tokens hacen falta, nunca los reemplaza.
    identidad_suficiente = len(tokens_compartidos) >= 1 and (
        misma_marca or len(tokens_compartidos) >= MINIMO_TOKENS_NOMBRE_SIN_MARCA
    )

    if misma_subcategoria and identidad_suficiente and not hay_asimetria_unidad_venta:
        partes = ["misma subcategoría"]
        if misma_marca:
            partes.append("misma marca")
        if comparacion["coincidencias"]:
            partes.append("coincide " + ", ".join(comparacion["coincidencias"]))
        return (CONFIRMADA, "Confirmado por " + ", ".join(partes), comparacion)

    return (PROBABLE, "Producto relacionado, sin confirmación completa", comparacion)


def _precio_utilizable(producto):
    precio = producto.get("precio")
    return precio if isinstance(precio, (int, float)) and precio > 0 else None


def _evaluar_renglon(item):
    """Para un renglón del proyecto: costo actual, y si existe, la mejor
    alternativa CONFIRMADA más barata (nunca una "probable" ni una sin
    clasificar, aunque sea más barata -- ver regla 5 del alcance)."""

    cantidad = item["cantidad"]
    precio_item = item["precio_actual"] if item["precio_actual"] is not None else item["precio_al_agregar"]
    costo_actual = round((precio_item or 0) * cantidad, 2)

    objetivo = {
        "proveedor": item["proveedor"],
        "id_proveedor": item["id_proveedor"],
        "nombre": item["nombre"],
        "categoria": item["categoria"],
        "precio": precio_item,
    }

    candidatos = obtener_similares(item["proveedor"], item["id_proveedor"], depurar=True)

    alternativas_confirmadas = []
    alternativas_probables = []

    for candidato in candidatos:
        precio_candidato = _precio_utilizable(candidato)
        if precio_candidato is None:
            continue

        nivel, razon, _ = clasificar_equivalencia(objetivo, candidato)

        entrada = {
            "proveedor": candidato["proveedor"],
            "id_proveedor": candidato["id_proveedor"],
            "nombre": candidato["nombre"],
            "precio": precio_candidato,
            "nivel_confianza": nivel,
            "razon": razon,
        }

        if nivel == CONFIRMADA:
            alternativas_confirmadas.append(entrada)
        elif nivel == PROBABLE:
            alternativas_probables.append(entrada)

    mejor_confirmada = None
    if alternativas_confirmadas:
        mejor_confirmada = min(alternativas_confirmadas, key=lambda a: a["precio"])

    precio_desconocido = precio_item is None
    es_mas_barata = (
        mejor_confirmada is not None
        and not precio_desconocido
        and mejor_confirmada["precio"] < precio_item
    )

    # Antes: si precio_item era None (sin precio_actual ni precio_al_agregar),
    # una alternativa confirmada real quedaba oculta y el ahorro se mostraba
    # como 0 -- indistinguible de "ya tenés el mejor precio". Son cosas
    # distintas: acá "no se puede calcular" (None), no "no hay ahorro" (0).
    # Se sigue mostrando la alternativa confirmada aunque no se pueda decir
    # cuánto se ahorra, en vez de ocultarla por completo.
    if precio_desconocido:
        alternativa_recomendada = mejor_confirmada
        ahorro_renglon = None
        precio_optimizado_unitario = mejor_confirmada["precio"] if mejor_confirmada else None
    elif es_mas_barata:
        alternativa_recomendada = mejor_confirmada
        ahorro_renglon = round((precio_item - mejor_confirmada["precio"]) * cantidad, 2)
        precio_optimizado_unitario = mejor_confirmada["precio"]
    else:
        alternativa_recomendada = None
        ahorro_renglon = 0
        precio_optimizado_unitario = precio_item

    costo_optimizado = round((precio_optimizado_unitario or 0) * cantidad, 2)

    return {
        "item_id": item["id"],
        "nombre": item["nombre"],
        "proveedor_actual": item["proveedor"],
        "precio_actual_unitario": precio_item,
        "cantidad": cantidad,
        "costo_actual": costo_actual,
        "disponible": item["disponible"],
        "alternativa_recomendada": alternativa_recomendada,
        "ahorro_renglon": ahorro_renglon,
        "costo_optimizado": costo_optimizado,
        "tiene_comparacion_segura": mejor_confirmada is not None,
        "alternativas_confirmadas": alternativas_confirmadas,
        "alternativas_probables": alternativas_probables,
        "mensaje": None if alternativas_confirmadas else MENSAJE_SIN_ALTERNATIVA_SEGURA,
    }


def calcular_presupuesto(proyecto_id, propietario_id):
    # Import diferido para evitar un ciclo de imports a nivel de módulo.
    # Se usa obtener_proyecto() (la función pública, no _obtener_items
    # directamente) porque es la que valida que el proyecto pertenezca a
    # propietario_id -- llamar al helper interno sin ese chequeo dejaría
    # ver el presupuesto de un proyecto ajeno adivinando el id, el mismo
    # tipo de hueco que ya se corrigió antes en este proyecto.
    from api.repositorio_proyectos import obtener_proyecto

    proyecto = obtener_proyecto(proyecto_id, propietario_id=propietario_id)

    if proyecto is None:
        return None

    # Solo renglones pendientes: lo ya comprado no tiene ahorro que buscar,
    # y lo descartado ya se excluye de los totales en todo el resto del
    # sistema (mismo criterio que _calcular_totales en repositorio_proyectos).
    items = [item for item in proyecto["items"] if item["estado"] == "pendiente"]

    if not items:
        return {
            "costo_actual": 0,
            "costo_optimizado_confirmado": 0,
            "ahorro_confirmado": 0,
            "ahorro_porcentual": 0,
            "items_sin_comparacion_segura": 0,
            "total_items": 0,
            "detalle": [],
        }

    detalle = [_evaluar_renglon(item) for item in items]

    costo_actual = round(sum(r["costo_actual"] for r in detalle), 2)
    costo_optimizado = round(sum(r["costo_optimizado"] for r in detalle), 2)
    ahorro_confirmado = round(costo_actual - costo_optimizado, 2)
    ahorro_porcentual = round((ahorro_confirmado / costo_actual) * 100, 2) if costo_actual else 0

    items_sin_comparacion_segura = sum(1 for r in detalle if not r["tiene_comparacion_segura"])

    return {
        "costo_actual": costo_actual,
        "costo_optimizado_confirmado": costo_optimizado,
        "ahorro_confirmado": ahorro_confirmado,
        "ahorro_porcentual": ahorro_porcentual,
        "items_sin_comparacion_segura": items_sin_comparacion_segura,
        "total_items": len(detalle),
        "detalle": detalle,
    }
