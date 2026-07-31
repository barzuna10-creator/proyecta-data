import sqlite3

from db import BASE_DATOS


def main():
    conexion = sqlite3.connect(BASE_DATOS)
    conexion.execute("PRAGMA foreign_keys = ON")
    cursor = conexion.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS proyectos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            propietario_id TEXT NOT NULL,
            nombre TEXT NOT NULL,
            comentario TEXT,
            estado TEXT NOT NULL DEFAULT 'activo',
            fecha_objetivo TEXT,
            token_compartido TEXT UNIQUE NOT NULL,
            fecha_creacion TEXT,
            fecha_actualizacion TEXT
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_proyectos_propietario
        ON proyectos (propietario_id)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items_proyecto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proyecto_id INTEGER NOT NULL REFERENCES proyectos(id) ON DELETE CASCADE,
            proveedor TEXT NOT NULL,
            id_proveedor TEXT NOT NULL,
            cantidad REAL NOT NULL DEFAULT 1,
            unidad_medida TEXT,
            estado TEXT NOT NULL DEFAULT 'pendiente',
            prioridad TEXT,
            comentario TEXT,
            nombre_al_agregar TEXT NOT NULL,
            marca_al_agregar TEXT,
            categoria_al_agregar TEXT,
            precio_al_agregar REAL,
            url_imagen_al_agregar TEXT,
            url_producto_al_agregar TEXT,
            fecha_agregado TEXT,
            UNIQUE(proyecto_id, proveedor, id_proveedor)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_items_proyecto_proyecto
        ON items_proyecto (proyecto_id)
    """)

    conexion.commit()
    conexion.close()

    print("✅ Tablas de proyectos creadas correctamente.")


if __name__ == "__main__":
    main()
