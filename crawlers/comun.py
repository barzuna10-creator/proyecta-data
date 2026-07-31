"""Código compartido entre los scrapers de proveedores.

Cada proveedor tiene su propia forma de descargar y normalizar productos,
pero todos terminan guardando el mismo esquema de columnas en la misma
base de datos. Esa parte común vive aquí para no reescribirla por
proveedor.
"""

import sqlite3

from db import BASE_DATOS


def guardar_productos(productos):
    conexion = sqlite3.connect(BASE_DATOS)
    cursor = conexion.cursor()

    guardados = 0

    for producto in productos:
        cursor.execute(
            """
            INSERT INTO productos (
                proveedor,
                id_proveedor,
                sku,
                nombre,
                marca,
                categoria,
                subcategoria,
                precio,
                iva,
                cabys,
                descripcion,
                url_imagen,
                url_producto,
                compra_online,
                fecha_actualizacion
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(proveedor, id_proveedor)
            DO UPDATE SET
                sku = excluded.sku,
                nombre = excluded.nombre,
                marca = excluded.marca,
                categoria = excluded.categoria,
                subcategoria = excluded.subcategoria,
                precio = excluded.precio,
                iva = excluded.iva,
                cabys = excluded.cabys,
                descripcion = excluded.descripcion,
                url_imagen = excluded.url_imagen,
                url_producto = excluded.url_producto,
                compra_online = excluded.compra_online,
                fecha_actualizacion = excluded.fecha_actualizacion
            """,
            (
                producto["proveedor"],
                producto["id_proveedor"],
                producto["sku"],
                producto["nombre"],
                producto["marca"],
                producto["categoria"],
                producto["subcategoria"],
                producto["precio"],
                producto["iva"],
                producto["cabys"],
                producto["descripcion"],
                producto["url_imagen"],
                producto["url_producto"],
                producto["compra_online"],
                producto["fecha_actualizacion"],
            ),
        )

        guardados += 1

    conexion.commit()
    conexion.close()

    return guardados
