from db import conectar
from busqueda import reconstruir_indice


def main(conexion=None):
    propia = conexion is None
    if propia:
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

    print("✅ Tabla productos_fts creada.")

    cantidad = reconstruir_indice(conexion)
    if propia:
        conexion.commit()
        conexion.close()
    print(f"✅ Índice reconstruido: {cantidad} productos indexados.")
    return {"productos_indexados": cantidad}


if __name__ == "__main__":
    main()
