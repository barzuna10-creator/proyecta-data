"""Verificaciones automáticas post-ingesta del catálogo. Pensado para
correr después de `main.py` y/o `actualizar_ellagar.py`.

Revisa:
1. Cantidad de productos por proveedor.
2. Productos con precio nulo o inválido (<= 0).
3. Productos sin nombre.
4. Duplicados por (proveedor, id_proveedor) -- no deberían existir nunca,
   dado el UNIQUE que usa el ON CONFLICT de guardar_productos, pero se
   verifica en vivo en vez de asumirlo.
5. Que el índice FTS5 (productos_fts) tenga la misma cantidad de filas que
   la tabla productos -- si no coincide, el índice quedó desincronizado
   (el mismo tipo de problema que causó el bug de reconstruir_indice()).
6. Una búsqueda real de control ("taladro") que debe devolver resultados.

No usa un framework de pruebas -- sigue el mismo estilo de script ejecutable
que pruebas_regresion_busqueda.py. Termina con código de salida distinto de
0 si algo crítico falla.
"""

import sqlite3
import sys

from db import BASE_DATOS
from busqueda import buscar_fts

TERMINO_DE_CONTROL = "taladro"
MINIMO_RESULTADOS_CONTROL = 5


def _conectar():
    conexion = sqlite3.connect(BASE_DATOS)
    conexion.row_factory = sqlite3.Row
    return conexion


def verificar_cantidad_por_proveedor(cursor):
    print("--- 1. Cantidad de productos por proveedor ---")
    cursor.execute(
        "SELECT proveedor, COUNT(*) AS total FROM productos GROUP BY proveedor ORDER BY proveedor"
    )
    filas = cursor.fetchall()

    if not filas:
        print("  ❌ La tabla productos está vacía.")
        return False

    for fila in filas:
        print(f"  {fila['proveedor']:<20} {fila['total']:>6}")

    return True


def verificar_precios_invalidos(cursor):
    print("\n--- 2. Productos con precio nulo o inválido ---")
    cursor.execute(
        "SELECT proveedor, COUNT(*) AS total FROM productos "
        "WHERE precio IS NULL OR precio <= 0 GROUP BY proveedor ORDER BY proveedor"
    )
    filas = cursor.fetchall()

    if not filas:
        print("  ✅ Ninguno.")
        return True

    total = sum(fila["total"] for fila in filas)
    for fila in filas:
        print(f"  {fila['proveedor']:<20} {fila['total']:>6}")
    print(f"  ⚠️  {total} productos con precio nulo o <= 0 (no bloquea, pero revisar).")
    return True


def verificar_nombres_vacios(cursor):
    print("\n--- 3. Productos sin nombre ---")
    cursor.execute(
        "SELECT proveedor, COUNT(*) AS total FROM productos "
        "WHERE nombre IS NULL OR TRIM(nombre) = '' GROUP BY proveedor"
    )
    filas = cursor.fetchall()

    if not filas:
        print("  ✅ Ninguno.")
        return True

    for fila in filas:
        print(f"  ❌ {fila['proveedor']:<20} {fila['total']:>6}")
    return False


def verificar_duplicados(cursor):
    print("\n--- 4. Duplicados por (proveedor, id_proveedor) ---")
    cursor.execute(
        "SELECT proveedor, id_proveedor, COUNT(*) AS total FROM productos "
        "GROUP BY proveedor, id_proveedor HAVING COUNT(*) > 1"
    )
    filas = cursor.fetchall()

    if not filas:
        print("  ✅ Ninguno.")
        return True

    for fila in filas[:10]:
        print(f"  ❌ {fila['proveedor']} / {fila['id_proveedor']}: {fila['total']} filas")
    if len(filas) > 10:
        print(f"  ... y {len(filas) - 10} más")
    return False


def verificar_indice_fts(cursor):
    print("\n--- 5. Estado del índice FTS5 ---")
    cursor.execute("SELECT COUNT(*) AS total FROM productos")
    total_productos = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) AS total FROM productos_fts")
    total_indexados = cursor.fetchone()["total"]

    print(f"  productos: {total_productos}  |  productos_fts: {total_indexados}")

    if total_productos != total_indexados:
        print(
            "  ❌ El índice está desincronizado -- correr "
            "`from busqueda import reconstruir_indice; reconstruir_indice()`."
        )
        return False

    print("  ✅ Coinciden.")
    return True


def verificar_busqueda_real(cursor):
    print(f"\n--- 6. Búsqueda de control: {TERMINO_DE_CONTROL!r} ---")
    resultados = buscar_fts(TERMINO_DE_CONTROL, limite=50)

    print(f"  {len(resultados)} resultados")

    if len(resultados) < MINIMO_RESULTADOS_CONTROL:
        print(
            f"  ❌ Se esperaban al menos {MINIMO_RESULTADOS_CONTROL}, "
            "el buscador puede estar roto o el índice vacío."
        )
        return False

    print(f"  ✅ Primer resultado: {resultados[0]['nombre']!r}")
    return True


def main():
    conexion = _conectar()
    cursor = conexion.cursor()

    checks = [
        verificar_cantidad_por_proveedor(cursor),
        verificar_precios_invalidos(cursor),
        verificar_nombres_vacios(cursor),
        verificar_duplicados(cursor),
        verificar_indice_fts(cursor),
        verificar_busqueda_real(cursor),
    ]

    conexion.close()

    print("\n=== Resumen ===")
    if all(checks):
        print("✅ Todas las verificaciones pasaron.")
        return 0

    print("❌ Alguna verificación crítica falló -- revisar arriba.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
