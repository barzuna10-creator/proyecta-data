import sqlite3

from db import BASE_DATOS
from busqueda import reconstruir_indice


def main():
    conexion = sqlite3.connect(BASE_DATOS)

    conexion.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS productos_fts USING fts5(
            nombre,
            categoria,
            subcategoria,
            content=''
        )
        """
    )

    conexion.commit()
    conexion.close()

    print("✅ Tabla productos_fts creada.")

    cantidad = reconstruir_indice()
    print(f"✅ Índice reconstruido: {cantidad} productos indexados.")


if __name__ == "__main__":
    main()
