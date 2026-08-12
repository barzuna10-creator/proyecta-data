"""Migración aditiva: índice sobre productos.url_producto (ver
id_producto.py, GET /productos/{id}). Antes solo estaban indexados
(proveedor, id_proveedor), categoria, familia_id y equivalencia_id -- una
búsqueda por url_producto (la llave que usa /producto/{id} para
reconstruir un producto desde un link compartido/recargado) hacía un
escaneo completo de la tabla (60k+ filas). No es un cambio de esquema
nuevo -- url_producto ya existe en la tabla desde siempre, esto solo la
indexa.

No se toca ninguna columna existente. Reejecutar este script no debe
fallar ni duplicar nada."""

from db import conectar


def main(conexion=None):
    propia = conexion is None
    if propia:
        conexion = conectar()
    conexion.execute(
        "CREATE INDEX IF NOT EXISTS idx_producto_url ON productos (url_producto)"
    )
    if propia:
        conexion.commit()
        conexion.close()

    print("✅ Índice de url_producto listo.")


if __name__ == "__main__":
    main()
