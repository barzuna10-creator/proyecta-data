import sqlite3

BASE_DATOS = "database/proyecta.db"


def buscar():
    conexion = sqlite3.connect(BASE_DATOS)
    cursor = conexion.cursor()

    texto = input("¿Qué producto desea buscar? ")

    cursor.execute(
        """
        SELECT
            nombre,
            precio,
            categoria,
            proveedor,
            url_producto
        FROM productos
        WHERE nombre LIKE ?
        ORDER BY precio ASC
        LIMIT 20
        """,
        (f"%{texto}%",),
    )

    resultados = cursor.fetchall()

    if not resultados:
        print("\nNo se encontraron productos.")
        conexion.close()
        return

    print("\n===== RESULTADOS =====\n")

    for nombre, precio, categoria, proveedor, url in resultados:
        print(nombre)
        print(f"₡ {precio:,.0f}")
        print(categoria)
        print(proveedor)
        print(url)
        print("-" * 60)

    conexion.close()


if __name__ == "__main__":
    buscar()