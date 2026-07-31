import sqlite3

import pandas as pd


ARCHIVO_EXCEL = "proyecta_catalogo_normalizado.xlsx"
BASE_DATOS = "database/proyecta.db"


def convertir_booleano(valor):
    if pd.isna(valor):
        return None

    return 1 if bool(valor) else 0


def main():
    print("Leyendo catálogo normalizado...")

    df = pd.read_excel(ARCHIVO_EXCEL)

    conexion = sqlite3.connect(BASE_DATOS)
    cursor = conexion.cursor()

    productos_guardados = 0

    for _, producto in df.iterrows():
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
            """,
            (
                producto.get("Proveedor"),
                producto.get("ID_Proveedor"),
                producto.get("SKU"),
                producto.get("Nombre"),
                producto.get("Marca"),
                producto.get("Categoria"),
                producto.get("Subcategoria"),
                producto.get("Precio_CRC"),
                convertir_booleano(producto.get("IVA")),
                producto.get("CABYS"),
                producto.get("Descripcion"),
                producto.get("URL_Imagen"),
                producto.get("URL_Producto"),
                convertir_booleano(producto.get("Compra_Online")),
                producto.get("Fecha_Actualizacion"),
            ),
        )

        productos_guardados += 1

    conexion.commit()
    conexion.close()

    print(f"✅ Productos guardados: {productos_guardados}")


if __name__ == "__main__":
    main()