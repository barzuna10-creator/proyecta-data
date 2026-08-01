import secrets
from datetime import datetime

from db import conectar

ESTADOS_PROYECTO = {"activo", "completado", "archivado"}
ESTADOS_ITEM = {"pendiente", "comprado", "descartado"}


def _ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generar_token():
    return secrets.token_urlsafe(9)


def _calcular_totales(items):
    total_pendiente = 0
    total_comprado = 0

    for item in items:
        precio = item["precio_actual"]
        if precio is None:
            precio = item["precio_al_agregar"] or 0

        subtotal = item["cantidad"] * precio

        if item["estado"] == "comprado":
            total_comprado += subtotal
        elif item["estado"] != "descartado":
            total_pendiente += subtotal

    return round(total_pendiente), round(total_comprado)


# Orden de construcción típico -- las partidas que coinciden con esta lista
# (ver también app/lib/partidas.ts en el frontend, misma lista) se muestran
# en este orden en vez de alfabético, porque así es como se lee una
# cotización real: de cimentación hacia acabados, no A-Z. Cualquier otra
# partida (texto libre del usuario) se ordena alfabéticamente después de
# estas, y "Sin partida" siempre queda de último -- es la señal visual de
# "esto todavía no lo he organizado".
ORDEN_PARTIDAS_SUGERIDAS = [
    "Cimentación", "Estructura", "Paredes", "Techo",
    "Eléctrico", "Hidráulico", "Acabados", "Pintura", "Otros",
]
SIN_PARTIDA = "Sin partida"


def _clave_orden_partida(nombre_partida):
    if nombre_partida == SIN_PARTIDA:
        return (2, "")
    if nombre_partida in ORDEN_PARTIDAS_SUGERIDAS:
        return (0, ORDEN_PARTIDAS_SUGERIDAS.index(nombre_partida))
    return (1, nombre_partida.lower())


def _agrupar_por_partida(items):
    """Agrupa los ítems de un proyecto (materiales, por ahora) en partidas
    para la cotización. Ítems descartados no forman parte de la cotización
    -- ya se excluyen en todo el resto del sistema (ver _calcular_totales).

    Devuelto como lista de dicts {partida, items, subtotal}, sin depender
    de nada específico de "material" más allá de cantidad/precio/partida --
    cuando exista mano de obra, sumarla a esta misma agrupación no debería
    requerir cambiar esta función, solo pasarle una lista de renglones que
    también tengan esos tres campos (ver COTIZACIONES_V1.md)."""

    grupos = {}

    for item in items:
        if item["estado"] == "descartado":
            continue

        precio = item["precio_actual"]
        if precio is None:
            precio = item["precio_al_agregar"] or 0

        nombre_partida = item["partida"] or SIN_PARTIDA
        grupo = grupos.setdefault(
            nombre_partida, {"partida": nombre_partida, "items": [], "subtotal": 0}
        )
        grupo["items"].append(item)
        grupo["subtotal"] += item["cantidad"] * precio

    for grupo in grupos.values():
        grupo["subtotal"] = round(grupo["subtotal"], 2)

    return sorted(grupos.values(), key=lambda g: _clave_orden_partida(g["partida"]))


def _calcular_cotizacion(proyecto, items):
    """Desglose completo de la cotización: partidas con subtotal, y encima
    de la suma de todas ellas, los tres porcentajes configurables del
    proyecto (indirectos, imprevistos, margen) aplicados como % planos
    sobre el mismo subtotal -- no en cascada uno sobre otro. Es una
    simplificación deliberada y documentada (ver COTIZACIONES_V1.md): hay
    firmas que sí calculan en cascada, pero un % plano y transparente sobre
    la misma base es más fácil de auditar a simple vista, y no había una
    única convención "correcta" que asumir sin inventar un criterio propio."""

    partidas = _agrupar_por_partida(items)
    subtotal_materiales = round(sum(p["subtotal"] for p in partidas), 2)

    indirectos = round(subtotal_materiales * (proyecto["indirectos_porcentaje"] or 0) / 100, 2)
    imprevistos = round(subtotal_materiales * (proyecto["imprevistos_porcentaje"] or 0) / 100, 2)
    margen = round(subtotal_materiales * (proyecto["margen_porcentaje"] or 0) / 100, 2)
    total_final = round(subtotal_materiales + indirectos + imprevistos + margen, 2)

    area_m2 = proyecto.get("area_m2")
    costo_por_m2 = round(total_final / area_m2, 2) if area_m2 else None

    return {
        "partidas": partidas,
        "subtotal_materiales": subtotal_materiales,
        "indirectos": indirectos,
        "imprevistos": imprevistos,
        "margen": margen,
        "total_final": total_final,
        "costo_por_m2": costo_por_m2,
    }


def _obtener_items(conexion, proyecto_id):
    cursor = conexion.execute(
        """
        SELECT
            i.id, i.proveedor, i.id_proveedor, i.cantidad, i.unidad_medida,
            i.estado, i.prioridad, i.comentario, i.fecha_agregado, i.partida,
            i.precio_al_agregar,
            COALESCE(pr.nombre, i.nombre_al_agregar) AS nombre,
            COALESCE(pr.marca, i.marca_al_agregar) AS marca,
            COALESCE(pr.categoria, i.categoria_al_agregar) AS categoria,
            COALESCE(pr.url_imagen, i.url_imagen_al_agregar) AS url_imagen,
            COALESCE(pr.url_producto, i.url_producto_al_agregar) AS url_producto,
            pr.precio AS precio_actual,
            (pr.id IS NOT NULL) AS disponible
        FROM items_proyecto i
        LEFT JOIN productos pr
            ON pr.proveedor = i.proveedor AND pr.id_proveedor = i.id_proveedor
        WHERE i.proyecto_id = ?
        ORDER BY i.fecha_agregado ASC
        """,
        (proyecto_id,),
    )

    items = []
    for fila in cursor.fetchall():
        item = dict(fila)
        item["disponible"] = bool(item["disponible"])
        items.append(item)

    return items


def crear_proyecto(propietario_id, nombre, comentario=None, fecha_objetivo=None):
    ahora = _ahora()
    token = _generar_token()

    conexion = conectar()
    cursor = conexion.execute(
        """
        INSERT INTO proyectos (
            propietario_id, nombre, comentario, estado,
            fecha_objetivo, token_compartido, fecha_creacion, fecha_actualizacion
        ) VALUES (?, ?, ?, 'activo', ?, ?, ?, ?)
        """,
        (propietario_id, nombre, comentario, fecha_objetivo, token, ahora, ahora),
    )
    proyecto_id = cursor.lastrowid
    conexion.commit()
    conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def listar_proyectos(propietario_id, incluir_archivados=False):
    conexion = conectar()

    condicion = "" if incluir_archivados else "AND p.estado != 'archivado'"

    cursor = conexion.execute(
        f"""
        SELECT
            p.id, p.nombre, p.cliente, p.estado, p.fecha_objetivo, p.fecha_actualizacion,
            COUNT(i.id) AS cantidad_items
        FROM proyectos p
        LEFT JOIN items_proyecto i
            ON i.proyecto_id = p.id AND i.estado != 'descartado'
        WHERE p.propietario_id = ? {condicion}
        GROUP BY p.id
        ORDER BY p.fecha_actualizacion DESC
        """,
        (propietario_id,),
    )

    resumenes = []
    for fila in cursor.fetchall():
        resumen = dict(fila)
        items = _obtener_items(conexion, resumen["id"])
        total_pendiente, total_comprado = _calcular_totales(items)
        resumen["total_pendiente"] = total_pendiente
        resumen["total_comprado"] = total_comprado
        resumenes.append(resumen)

    conexion.close()
    return resumenes


def obtener_proyecto(proyecto_id=None, propietario_id=None, token=None):
    conexion = conectar()

    if token is not None:
        fila = conexion.execute(
            "SELECT * FROM proyectos WHERE token_compartido = ?", (token,)
        ).fetchone()
    else:
        fila = conexion.execute(
            "SELECT * FROM proyectos WHERE id = ? AND propietario_id = ?",
            (proyecto_id, propietario_id),
        ).fetchone()

    if not fila:
        conexion.close()
        return None

    proyecto = dict(fila)
    items = _obtener_items(conexion, proyecto["id"])
    conexion.close()

    total_pendiente, total_comprado = _calcular_totales(items)

    proyecto["items"] = items
    proyecto["total_pendiente"] = total_pendiente
    proyecto["total_comprado"] = total_comprado
    proyecto["cotizacion"] = _calcular_cotizacion(proyecto, items)

    return proyecto


def actualizar_proyecto(proyecto_id, propietario_id, cambios):
    campos_validos = {
        "nombre", "comentario", "estado", "fecha_objetivo",
        "cliente", "direccion", "area_m2",
        "indirectos_porcentaje", "imprevistos_porcentaje", "margen_porcentaje",
    }
    cambios = {k: v for k, v in cambios.items() if k in campos_validos and v is not None}

    if "estado" in cambios and cambios["estado"] not in ESTADOS_PROYECTO:
        raise ValueError(f"Estado inválido: {cambios['estado']}")

    if not cambios:
        return obtener_proyecto(proyecto_id, propietario_id=propietario_id)

    conexion = conectar()
    fila = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()

    if not fila:
        conexion.close()
        return None

    cambios["fecha_actualizacion"] = _ahora()
    asignaciones = ", ".join(f"{campo} = ?" for campo in cambios)

    conexion.execute(
        f"UPDATE proyectos SET {asignaciones} WHERE id = ?",
        (*cambios.values(), proyecto_id),
    )
    conexion.commit()
    conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def eliminar_proyecto(proyecto_id, propietario_id):
    conexion = conectar()
    fila = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()

    if not fila:
        conexion.close()
        return False

    conexion.execute("DELETE FROM proyectos WHERE id = ?", (proyecto_id,))
    conexion.commit()
    conexion.close()

    return True


def agregar_item(proyecto_id, propietario_id, proveedor, id_proveedor, cantidad=1):
    conexion = conectar()

    proyecto = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()

    if not proyecto:
        conexion.close()
        return None

    producto = conexion.execute(
        """
        SELECT nombre, marca, categoria, precio, url_imagen, url_producto
        FROM productos
        WHERE proveedor = ? AND id_proveedor = ?
        """,
        (proveedor, id_proveedor),
    ).fetchone()

    if not producto:
        conexion.close()
        raise ValueError("Producto no encontrado en el catálogo")

    ahora = _ahora()

    conexion.execute(
        """
        INSERT INTO items_proyecto (
            proyecto_id, proveedor, id_proveedor, cantidad,
            nombre_al_agregar, marca_al_agregar, categoria_al_agregar,
            precio_al_agregar, url_imagen_al_agregar, url_producto_al_agregar,
            fecha_agregado
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(proyecto_id, proveedor, id_proveedor)
        DO UPDATE SET
            -- Si el ítem ya existía pendiente, sumar es lo esperado (agregar
            -- "2 más" de algo que ya estaba en la lista). Pero si estaba
            -- descartado o comprado, sumarle a esa cantidad vieja no tiene
            -- sentido -- el usuario está agregándolo de nuevo, no ajustando
            -- una cantidad activa. Antes de este fix, reagregar un ítem
            -- descartado no lo reactivaba (seguía sin aparecer en ningún
            -- total) sin ningún error visible.
            cantidad = CASE
                WHEN estado = 'pendiente' THEN cantidad + excluded.cantidad
                ELSE excluded.cantidad
            END,
            estado = 'pendiente'
        """,
        (
            proyecto_id, proveedor, id_proveedor, cantidad,
            producto["nombre"], producto["marca"], producto["categoria"],
            producto["precio"], producto["url_imagen"], producto["url_producto"],
            ahora,
        ),
    )

    conexion.execute(
        "UPDATE proyectos SET fecha_actualizacion = ? WHERE id = ?",
        (ahora, proyecto_id),
    )

    conexion.commit()
    conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def actualizar_item(proyecto_id, propietario_id, item_id, cambios):
    campos_validos = {"cantidad", "estado", "prioridad", "comentario", "partida"}
    cambios = {k: v for k, v in cambios.items() if k in campos_validos and v is not None}

    if "estado" in cambios and cambios["estado"] not in ESTADOS_ITEM:
        raise ValueError(f"Estado inválido: {cambios['estado']}")

    conexion = conectar()

    proyecto = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()

    if not proyecto:
        conexion.close()
        return None

    if cambios:
        asignaciones = ", ".join(f"{campo} = ?" for campo in cambios)
        conexion.execute(
            f"UPDATE items_proyecto SET {asignaciones} WHERE id = ? AND proyecto_id = ?",
            (*cambios.values(), item_id, proyecto_id),
        )
        conexion.execute(
            "UPDATE proyectos SET fecha_actualizacion = ? WHERE id = ?",
            (_ahora(), proyecto_id),
        )
        conexion.commit()

    conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def eliminar_item(proyecto_id, propietario_id, item_id):
    conexion = conectar()

    proyecto = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()

    if not proyecto:
        conexion.close()
        return None

    conexion.execute(
        "DELETE FROM items_proyecto WHERE id = ? AND proyecto_id = ?",
        (item_id, proyecto_id),
    )
    conexion.execute(
        "UPDATE proyectos SET fecha_actualizacion = ? WHERE id = ?",
        (_ahora(), proyecto_id),
    )
    conexion.commit()
    conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)
