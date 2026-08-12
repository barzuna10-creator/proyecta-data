"""Migración aditiva para la integración lector de planos <-> Proyecta
(ver INTEGRACION_LECTURA_PLANOS_PROYECTO.md):

`proyectos` gana tres columnas para guardar el resultado de analizar UN
PDF de planos con `lectura_planos` (niveles/espacios/láminas ya
extraídos, serializados a JSON) -- mismo patrón que ya usa
`productos.imagenes_adicionales` (columna TEXT + json.dumps/json.loads
manual, ver api/main.py). El PDF original no se guarda, solo el
resultado ya estructurado del análisis.

No se toca ninguna columna existente. Reejecutar este script no debe
fallar ni duplicar nada.
"""

from db import conectar

COLUMNAS_PROYECTOS = [
    ("plano_nombre_archivo", "TEXT"),
    ("plano_analisis", "TEXT"),
    ("plano_fecha_analisis", "TEXT"),
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
    _agregar_columnas_faltantes(cursor, "proyectos", COLUMNAS_PROYECTOS)

    if propia:
        conexion.commit()
        conexion.close()

    print("✅ Esquema de plano de proyecto listo.")


if __name__ == "__main__":
    main()
