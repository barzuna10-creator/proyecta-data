from db import conectar
from familias import calcular_familias


def main():
    conexion = conectar()

    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS familias_producto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor TEXT NOT NULL,
            categoria TEXT NOT NULL,
            firma_base TEXT NOT NULL,
            nombre_familia TEXT NOT NULL,
            fecha_calculo TEXT NOT NULL,
            UNIQUE(proveedor, categoria, firma_base)
        )
        """
    )

    columnas = [fila[1] for fila in conexion.execute("PRAGMA table_info(productos)").fetchall()]
    if "familia_id" not in columnas:
        conexion.execute("ALTER TABLE productos ADD COLUMN familia_id INTEGER")

    conexion.commit()
    conexion.close()

    print("✅ Tabla familias_producto y columna productos.familia_id listas.")

    total_familias, total_productos = calcular_familias()
    print(f"✅ Familias calculadas: {total_familias} familias, {total_productos} productos agrupados.")


if __name__ == "__main__":
    main()
