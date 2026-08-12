"""Agrupación de productos en familias (primera versión: solo categoría Pinturas).

Detecta variantes de presentación (galón, cuarto, cubeta, etc.) del mismo
producto para poder mostrarlas agrupadas en una sola tarjeta en vez de como
resultados separados. Alcance intencionalmente limitado a CATEGORIAS_AGRUPABLES
hasta validar que funciona bien -- ver investigación previa sobre por qué
categorías como Tornillos, Cable o Tubo PVC no son seguras para esta misma
heurística (el número que varía ahí es una especificación funcional, no un
tamaño de empaque).
"""

from db import conectar
from busqueda import normalizar_texto

CATEGORIAS_AGRUPABLES = {"Pinturas"}

PRESENTACION_LABELS = {
    "gal": "Galón", "galon": "Galón", "galones": "Galón", "gln": "Galón",
    "cuarto": "Cuarto", "cuartos": "Cuarto",
    "cubeta": "Cubeta", "cubetas": "Cubeta", "cub": "Cubeta",
    "litro": "Litro", "litros": "Litro", "lt": "Litro", "lts": "Litro",
    "oz": "Oz", "onza": "Oz", "onzas": "Oz",
    "ml": "Ml", "kg": "Kg", "lb": "Lb", "libra": "Lb", "libras": "Lb", "g": "G",
}


def _es_numero_de_tamano(token):
    limpio = token.strip(".")
    if "/" in limpio:
        partes = limpio.split("/")
        return len(partes) == 2 and all(p.isdigit() for p in partes)
    return limpio.replace(",", "").isdigit()


def analizar_nombre(nombre):
    """Separa un nombre en palabras "core" (identidad del producto: marca,
    línea, color, acabado) y palabras de presentación (tamaño/empaque).

    Devuelve (firma_para_agrupar, nombre_familia, etiqueta_presentacion).
    """

    core = []
    presentacion = []

    for palabra in nombre.split():
        norm = normalizar_texto(palabra)
        if norm in PRESENTACION_LABELS:
            presentacion.append(PRESENTACION_LABELS[norm])
        elif _es_numero_de_tamano(norm):
            presentacion.append(palabra.strip("().,"))
        else:
            core.append(palabra)

    firma = normalizar_texto(" ".join(core))
    nombre_familia = " ".join(core).strip()
    etiqueta_presentacion = " ".join(presentacion).strip()

    return firma, nombre_familia, etiqueta_presentacion


def calcular_familias(conexion=None):
    """Recalcula familias_producto y productos.familia_id para las categorías
    en CATEGORIAS_AGRUPABLES. Conserva el mismo id de familia entre corridas
    para la misma combinación (proveedor, categoria, firma_base), para que
    familia_id sea estable si algún día se cachea o se usa en una URL."""

    propia = conexion is None
    if propia:
        conexion = conectar()

    # Limpiar primero: si una categoría sale de la lista blanca en el futuro,
    # sus productos no deben quedar con un familia_id obsoleto.
    conexion.execute("UPDATE productos SET familia_id = NULL")

    placeholders = ",".join("?" for _ in CATEGORIAS_AGRUPABLES)
    categorias = tuple(CATEGORIAS_AGRUPABLES)

    filas = conexion.execute(
        f"SELECT id, nombre, proveedor, categoria FROM productos WHERE categoria IN ({placeholders})",
        categorias,
    ).fetchall()

    grupos = {}
    for fila in filas:
        firma, nombre_familia, _ = analizar_nombre(fila["nombre"])
        clave = (fila["proveedor"], fila["categoria"], firma)
        grupos.setdefault(clave, {"nombre_familia": nombre_familia, "miembros": []})
        grupos[clave]["miembros"].append(fila["id"])

    total_familias = 0
    total_productos_agrupados = 0
    ids_familias_vigentes = []

    for (proveedor, categoria, firma), datos in grupos.items():
        if len(datos["miembros"]) < 2:
            continue  # sin variantes reales -- se queda sin agrupar (familia_id NULL)

        fila_familia = conexion.execute(
            "SELECT id FROM familias_producto WHERE proveedor=? AND categoria=? AND firma_base=?",
            (proveedor, categoria, firma),
        ).fetchone()

        if fila_familia:
            familia_id = fila_familia["id"]
            conexion.execute(
                "UPDATE familias_producto SET nombre_familia=?, fecha_calculo=datetime('now') WHERE id=?",
                (datos["nombre_familia"], familia_id),
            )
        else:
            cursor = conexion.execute(
                """
                INSERT INTO familias_producto
                    (proveedor, categoria, firma_base, nombre_familia, fecha_calculo)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (proveedor, categoria, firma, datos["nombre_familia"]),
            )
            familia_id = cursor.lastrowid

        ids_familias_vigentes.append(familia_id)
        conexion.executemany(
            "UPDATE productos SET familia_id = ? WHERE id = ?",
            [(familia_id, id_producto) for id_producto in datos["miembros"]],
        )

        total_familias += 1
        total_productos_agrupados += len(datos["miembros"])

    if ids_familias_vigentes:
        marcadores = ",".join("?" for _ in ids_familias_vigentes)
        conexion.execute(
            f"DELETE FROM familias_producto WHERE id NOT IN ({marcadores})",
            ids_familias_vigentes,
        )
    else:
        conexion.execute("DELETE FROM familias_producto")

    if propia:
        conexion.commit()
        conexion.close()

    return total_familias, total_productos_agrupados
