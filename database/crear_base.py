import sqlite3


conexion = sqlite3.connect("database/proyecta.db")

cursor = conexion.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS productos (

    id INTEGER PRIMARY KEY AUTOINCREMENT,

    proveedor TEXT,

    id_proveedor TEXT,

    sku TEXT,

    nombre TEXT,

    marca TEXT,

    categoria TEXT,

    subcategoria TEXT,

    precio REAL,

    iva REAL,

    cabys TEXT,

    descripcion TEXT,

    url_imagen TEXT,

    url_producto TEXT,

    compra_online INTEGER,

    fecha_actualizacion TEXT

)
""")

conexion.commit()

conexion.close()

print("✅ Base de datos creada correctamente.")