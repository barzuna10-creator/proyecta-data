"""Migración aditiva para la selección automática de productos desde un
plano (ver seleccion_automatica.py y api/repositorio_proyectos.py::
generar_cotizacion_automatica).

`items_proyecto` gana dos columnas:

- `confianza_match` (alta | media | baja | NULL): qué tan seguro está el
  motor de auto-selección de que el producto real elegido es el correcto
  para el material detectado en el plano. Deliberadamente DISTINTA de la
  columna `confianza` que ya existía (agregar_trazabilidad_items.py) --
  esa es confianza de LECTURA del plano (¿se leyó bien el cuadro de
  puertas?), esta es confianza de SELECCIÓN de producto (¿es este SKU el
  correcto?). Conflar las dos habría perdido información real: un
  material puede leerse con confianza alta y aun así no tener ningún
  producto del catálogo que lo represente bien.
- `revisado` (0 | 1, NOT NULL DEFAULT 1): si un humano ya confirmó,
  reemplazó o descartó este ítem. DEFAULT 1 a propósito -- todo ítem que
  ya existía antes de esta migración (agregado a mano, por definición ya
  "revisado" porque una persona lo eligió) sigue tratándose exactamente
  igual que hoy. Únicamente los ítems que crea el motor de selección
  automática nacen en 0, pendientes de que el usuario los apruebe,
  reemplace o elimine antes de dar la cotización por buena.

No se toca ninguna columna existente. Reejecutar este script no debe
fallar ni duplicar nada.
"""

from db import conectar

COLUMNAS_ITEMS_PROYECTO = [
    ("confianza_match", "TEXT"),
    ("revisado", "INTEGER NOT NULL DEFAULT 1"),
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

    print("✅ Esquema de selección automática listo.")


if __name__ == "__main__":
    main()
