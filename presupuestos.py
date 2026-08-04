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

from especificaciones import comparar_specs
from equivalencias import (
    UMBRALES_POR_MODULO,
    calcular_puntaje_equivalencia,
    es_equivalente_para,
    extraer_atributos,
)
from similares import obtener_similares

CONFIRMADA = "equivalencia_confirmada"
PROBABLE = "equivalencia_probable"
NO_COMPARABLE = "no_comparable"

# Piso de "probable": el mismo umbral que ya usa el comparador para decir
# "esto es lo mismo" lado a lado (UMBRALES_POR_MODULO["comparador"]) --
# suficiente para mostrarlo como relacionado, nunca para calcular con ese
# número un ahorro en dinero real (eso exige el umbral más alto de
# "presupuestos", ver es_equivalente_para más abajo).
UMBRAL_PROBABLE = UMBRALES_POR_MODULO["comparador"]

MENSAJE_SIN_ALTERNATIVA_SEGURA = (
    "No se encontraron alternativas comparables con suficiente confianza"
)


def clasificar_equivalencia(objetivo, candidato):
    """Decide qué tan confiable es tratar `candidato` como sustituto de
    `objetivo` para efectos de comparar precio. `candidato` ya viene de
    similares.obtener_similares(depurar=True) -- ya pasó el umbral mínimo
    de esa función, así que "no calificó ni como candidato" no es un caso
    que este clasificador tenga que manejar; eso ya lo filtró similares.py.

    Usa equivalencias.calcular_puntaje_equivalencia() -- el motor de
    confianza auditado contra el catálogo real (ver EQUIVALENCIAS.md,
    AUDITORIA_EQUIVALENCIAS.md, MOTOR_CONFIANZA.md) -- al umbral que
    UMBRALES_POR_MODULO define para "presupuestos" (0.85, el más alto de
    todos los módulos): acá se calcula un ahorro en dinero real, así que
    el listón para "confirmado" es deliberadamente más exigente que en el
    comparador o en similares.

    Reemplaza al clasificador anterior (basado en las razones de
    similares.py: misma_subcategoria/misma_marca/tokens_nombre), que no
    tenía ningún chequeo de compatibilidad física ni categórico -- bug
    real encontrado integrando esta función a la UI: "Grifo para ducha
    negro ebano" confirmaba como sustituto de "Cabeza para ducha redonda
    oslo negro mate" (dos partes de plomería distintas) solo por compartir
    subcategoría, categoría "Baños" y la palabra "ducha", con un ahorro
    fabricado de ₡20,200. El nuevo motor da 0.27 para ese mismo par
    (categoria+color coinciden, pero marca distinta y solo 2 tokens de 8
    compartidos) -- muy por debajo de ambos umbrales.

    Un segundo hallazgo, encontrado calibrando este reemplazo con casos
    reales: "Cemento Gris Portland UG 42.5 kg" vs "Cemento Gris Portland
    UG" (mismo nombre, pero al candidato no se le detectó el peso) puntúa
    1.0 -- el motor general no penaliza que el peso esté ausente de un
    solo lado (ausencia no es lo mismo que desacuerdo, ver equivalencias.py),
    correcto para búsqueda/comparador, pero acá sí importa: son dos bolsas
    de tamaño posiblemente distinto y este módulo calcula dinero real. Por
    eso, además del puntaje, se exige que no haya asimetría de unidad de
    venta (peso/volumen/presentación detectado en un lado y no en el otro)
    para llegar a CONFIRMADA -- si la hay, baja a PROBABLE aunque el
    puntaje ya haya cruzado el umbral.

    Devuelve (nivel, razon_legible, detalle)."""

    atributos_objetivo = extraer_atributos(
        objetivo["nombre"], objetivo.get("marca"), categoria=objetivo.get("categoria")
    )
    atributos_candidato = extraer_atributos(
        candidato["nombre"], candidato.get("marca"), categoria=candidato.get("categoria")
    )

    resultado = calcular_puntaje_equivalencia(atributos_objetivo, atributos_candidato)
    puntaje = resultado["puntaje"]

    comparacion_specs = comparar_specs(atributos_objetivo["specs"], atributos_candidato["specs"])
    hay_asimetria_unidad_venta = bool(comparacion_specs["asimetrias"])
    resultado["asimetria_unidad_venta"] = hay_asimetria_unidad_venta

    if es_equivalente_para("presupuestos", puntaje) and not hay_asimetria_unidad_venta:
        nivel = CONFIRMADA
    elif puntaje >= UMBRAL_PROBABLE:
        nivel = PROBABLE
    else:
        nivel = NO_COMPARABLE

    return nivel, resultado["explicacion"], resultado


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
        "marca": item.get("marca"),
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
    #
    # precio_optimizado_unitario se deja en None (no en el precio de la
    # alternativa) a propósito: costo_actual de este renglón ya es 0 por no
    # tener precio conocido, así que si costo_optimizado tomara el precio
    # real de la alternativa, el agregado de todo el proyecto restaría un
    # costo real contra un costo_actual ficticio de 0 -- un ahorro agregado
    # negativo sin sentido (encontrado escribiendo las pruebas de esta
    # fase). Con ambos en 0, el renglón no distorsiona el agregado; el
    # detalle por renglón sigue mostrando la alternativa igual.
    if precio_desconocido:
        alternativa_recomendada = mejor_confirmada
        ahorro_renglon = None
        precio_optimizado_unitario = None
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
