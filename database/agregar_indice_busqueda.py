from db import conectar
from busqueda import reconstruir_indice


def main():
    conexion = conectar()

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
