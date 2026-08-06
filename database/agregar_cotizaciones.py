"""Migración aditiva para la capa de cotización (ver COTIZACIONES_V1.md):

- `proyectos` gana datos de la ficha (cliente, dirección, área) y los tres
  porcentajes de la cotización (indirectos, imprevistos, margen).
- `items_proyecto` gana `partida`, para agrupar materiales en capítulos
  (Cimentación, Estructura, Acabados...) con subtotal por partida.

No se toca ninguna columna existente ni se borra nada -- todas las columnas
nuevas son opcionales o tienen un valor por defecto que reproduce el
comportamiento de antes de esta migración (0% en los tres porcentajes,
NULL en el resto). Reejecutar este script no debe fallar ni duplicar nada.
"""

from db import conectar

COLUMNAS_PROYECTOS = [
    ("cliente", "TEXT"),
    ("direccion", "TEXT"),
    ("area_m2", "REAL"),
    ("indirectos_porcentaje", "REAL NOT NULL DEFAULT 0"),
    ("imprevistos_porcentaje", "REAL NOT NULL DEFAULT 0"),
    ("margen_porcentaje", "REAL NOT NULL DEFAULT 0"),
]

COLUMNAS_ITEMS_PROYECTO = [
    ("partida", "TEXT"),
]


def _columnas_existentes(cursor, tabla):
    cursor.execute(f"PRAGMA table_info({tabla})")
    return {fila[1] for fila in cursor.fetchall()}


def _agregar_columnas_faltantes(cursor, tabla, columnas):
    existentes = _columnas_existentes(cursor, tabla)
    for nombre, definicion in columnas:
        if nombre in existentes:
            continue
        cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {definicion}")
        print(f"  + {tabla}.{nombre}")


def main():
    conexion = conectar()
    cursor = conexion.cursor()

    print("Columnas agregadas:")
    _agregar_columnas_faltantes(cursor, "proyectos", COLUMNAS_PROYECTOS)
    _agregar_columnas_faltantes(cursor, "items_proyecto", COLUMNAS_ITEMS_PROYECTO)

    conexion.commit()
    conexion.close()

    print("✅ Esquema de cotizaciones listo.")


if __name__ == "__main__":
    main()
