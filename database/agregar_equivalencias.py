import time

from db import conectar
from equivalencias import calcular_equivalencias


def main(conexion=None):
    propia = conexion is None
    if propia:
        conexion = conectar()

    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS grupos_equivalencia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            categoria TEXT NOT NULL,
            nombre_representativo TEXT NOT NULL,
            marca_representativa TEXT,
            cantidad_miembros INTEGER NOT NULL,
            cantidad_proveedores INTEGER NOT NULL,
            fecha_calculo TEXT NOT NULL
        )
        """
    )

    columnas = [fila[1] for fila in conexion.execute("PRAGMA table_info(productos)").fetchall()]
    if "equivalencia_id" not in columnas:
        conexion.execute("ALTER TABLE productos ADD COLUMN equivalencia_id INTEGER")
        conexion.execute(
            "CREATE INDEX IF NOT EXISTS idx_producto_equivalencia ON productos (equivalencia_id)"
        )

    print("✅ Tabla grupos_equivalencia y columna productos.equivalencia_id listas.")

    print("\nCargando catálogo completo...")
    t0 = time.time()
    productos = [
        dict(fila)
        for fila in conexion.execute(
            "SELECT id, proveedor, nombre, marca, categoria FROM productos"
        ).fetchall()
    ]
    print(f"{len(productos)} productos cargados en {time.time() - t0:.1f}s")

    print("\nCalculando equivalencias (esto recorre todo el catálogo, puede tardar varios minutos)...")
    t0 = time.time()
    grupos = calcular_equivalencias(productos)
    duracion = time.time() - t0
    print(f"{len(grupos)} grupos de equivalencia encontrados en {duracion:.1f}s")

    total_productos_agrupados = sum(g["cantidad_miembros"] for g in grupos)
    print(f"{total_productos_agrupados} productos quedaron en algún grupo.")

    print("\nGuardando en la base de datos...")
    conexion.execute("UPDATE productos SET equivalencia_id = NULL")
    conexion.execute("DELETE FROM grupos_equivalencia")

    ahora = time.strftime("%Y-%m-%d %H:%M:%S")
    for grupo in grupos:
        miembros = grupo["miembros"]
        # Representativo: el nombre más largo del grupo suele ser el más
        # descriptivo (mismo criterio simple que ya usa el resto del
        # proyecto para elegir un nombre "representativo" sin inventar
        # una heurística de calidad de texto nueva).
        representativo = max(miembros, key=lambda m: len(m["nombre"] or ""))
        cursor = conexion.execute(
            """
            INSERT INTO grupos_equivalencia
                (categoria, nombre_representativo, marca_representativa,
                 cantidad_miembros, cantidad_proveedores, fecha_calculo)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                representativo["categoria"],
                representativo["nombre"],
                representativo.get("marca"),
                grupo["cantidad_miembros"],
                grupo["cantidad_proveedores"],
                ahora,
            ),
        )
        equivalencia_id = cursor.lastrowid
        conexion.executemany(
            "UPDATE productos SET equivalencia_id = ? WHERE id = ?",
            [(equivalencia_id, m["id"]) for m in miembros],
        )

    if propia:
        conexion.commit()
        conexion.close()

    print(f"✅ {len(grupos)} grupos guardados, {total_productos_agrupados} productos vinculados.")
    return {
        "grupos_equivalencia": len(grupos),
        "productos_equivalentes": total_productos_agrupados,
    }


if __name__ == "__main__":
    main()
