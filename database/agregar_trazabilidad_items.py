"""Migración aditiva para trazabilidad de ítems (ver
AUDITORIA_INTEGRAL_PRODUCTO.md, hallazgo §1: "items_proyecto no guarda
de dónde vino un ítem"):

`items_proyecto` gana seis columnas, todas opcionales, para conservar
permanentemente de dónde salió cada renglón -- origen (plano | sistema_
constructivo | plantilla | manual), página y lámina fuente, texto
original tal como aparece en el plano, nivel de confianza, y qué regla o
extractor lo generó. Ningún dato existente se toca: los ítems
agregados antes de esta migración quedan con estas columnas en NULL --
"no se registró" es honesto, no se les inventa un origen retroactivo.

No se toca ninguna columna existente. Reejecutar este script no debe
fallar ni duplicar nada.
"""

from db import conectar

COLUMNAS_ITEMS_PROYECTO = [
    ("origen", "TEXT"),
    ("pagina_fuente", "INTEGER"),
    ("lamina_fuente", "TEXT"),
    ("texto_original", "TEXT"),
    ("confianza", "TEXT"),
    ("regla_generadora", "TEXT"),
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


def main(conexion=None):
    propia = conexion is None
    if propia:
        conexion = conectar()
    cursor = conexion.cursor()

    print("Columnas agregadas:")
    _agregar_columnas_faltantes(cursor, "items_proyecto", COLUMNAS_ITEMS_PROYECTO)

    if propia:
        conexion.commit()
        conexion.close()

    print("✅ Esquema de trazabilidad de ítems listo.")


if __name__ == "__main__":
    main()
