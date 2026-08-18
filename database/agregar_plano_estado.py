"""Migración aditiva para Mission #002 (Plan Processing Stability):
`proyectos` gana tres columnas para soportar el flujo asíncrono de
análisis de plano -- subir devuelve de inmediato, el cliente consulta el
estado por separado, en vez de que la petición HTTP quede bloqueada hasta
que el análisis termine (ver INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md,
sección 6, y la verificación directa contra producción de esta misión que
confirmó un 502 real a los 43s con un plano de 105MB -- 35% del límite
permitido -- sin que nada del lado del cliente se pudiera hacer para
evitarlo mientras el análisis siga ocurriendo dentro del ciclo de vida de
la propia petición HTTP).

- `plano_estado`: NULL (nunca se subió un plano) -> 'procesando' ->
  'listo' | 'error'.
- `plano_error_mensaje`: mensaje seguro para el usuario cuando
  plano_estado='error' -- nunca la excepción real (ver
  api/repositorio_proyectos.py).
- `plano_procesamiento_id`: token opaco por intento de subida, para que
  un resultado o timeout tardío de un intento viejo nunca pise el estado
  de uno más nuevo.

Backfill: cualquier proyecto que ya tenga un `plano_analisis` guardado (de
antes de esta migración) se marca 'listo' -- es exactamente lo que
significa tener un análisis ya guardado, sin necesidad de volver a
analizarlo. Mismo patrón que agregar_plano_proyecto.py: reejecutar este
script no debe fallar ni duplicar nada.
"""

from db import conectar

COLUMNAS_PROYECTOS = [
    ("plano_estado", "TEXT"),
    ("plano_error_mensaje", "TEXT"),
    ("plano_procesamiento_id", "TEXT"),
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


def _backfill_plano_listo(cursor):
    cursor.execute(
        "UPDATE proyectos SET plano_estado = 'listo' "
        "WHERE plano_analisis IS NOT NULL AND plano_estado IS NULL"
    )
    if cursor.rowcount:
        print(f"  ~ {cursor.rowcount} proyecto(s) con plano ya analizado marcados 'listo'")


def main():
    conexion = conectar()
    cursor = conexion.cursor()

    print("Columnas agregadas:")
    _agregar_columnas_faltantes(cursor, "proyectos", COLUMNAS_PROYECTOS)
    _backfill_plano_listo(cursor)

    conexion.commit()
    conexion.close()

    print("✅ Esquema de estado asíncrono de plano listo.")


if __name__ == "__main__":
    main()
