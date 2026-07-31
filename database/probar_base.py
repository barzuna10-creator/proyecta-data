import sqlite3

conexion = sqlite3.connect("database/proyecta.db")
cursor = conexion.cursor()

busqueda = input("¿Qué producto desea buscar? ")

cursor.execute("""
SELECT
    nombre,
    precio,
    categoria,
    proveedor
FROM productos
WHERE nombre LIKE ?
LIMIT 20
""", (f"%{busqueda}%",))

productos = cursor.fetchall()

print("\nResultados:\n")

for producto in productos:
    print(producto)

conexion.close()