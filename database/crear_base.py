"""Esquema fundacional de la base de datos.

Este módulo debe poder ejecutarse contra cualquier ``DATABASE_PATH``. Es la
primera migración del runner: las migraciones históricas agregan columnas e
índices sobre ``productos`` y no pueden crearla por sí mismas.
"""

from db import conectar


COLUMNAS_PRODUCTOS = {
    "id",
    "proveedor",
    "id_proveedor",
    "sku",
    "nombre",
    "marca",
    "categoria",
    "subcategoria",
    "precio",
    "iva",
    "cabys",
    "descripcion",
    "url_imagen",
    "url_producto",
    "compra_online",
    "fecha_actualizacion",
}


def main():
    conexion = conectar()
    try:
        conexion.execute(
            """
            CREATE TABLE IF NOT EXISTS productos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proveedor TEXT,
                id_proveedor TEXT,
                sku TEXT,
                nombre TEXT,
                marca TEXT,
                categoria TEXT,
                subcategoria TEXT,
                precio REAL,
                iva REAL,
                cabys TEXT,
                descripcion TEXT,
                url_imagen TEXT,
                url_producto TEXT,
                compra_online INTEGER,
                fecha_actualizacion TEXT
            )
            """
        )
        conexion.commit()
    finally:
        conexion.close()

    print("✅ Esquema fundacional creado correctamente.")


if __name__ == "__main__":
    main()
