import sqlite3


BASE_DATOS = "database/proyecta.db"


def main():
    conexion = sqlite3.connect(BASE_DATOS)
    cursor = conexion.cursor()

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_producto_proveedor
        ON productos (proveedor, id_proveedor)
    """)

    conexion.commit()
    conexion.close()

    print("✅ Base preparada para múltiples proveedores.")


if __name__ == "__main__":
    main()