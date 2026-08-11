"""Migración aditiva e idempotente para cálculo de compra P0-01."""

from db import conectar


VERSION_CALCULO_ACTUAL = 2

COLUMNAS_PROYECTOS = [
    ("version_calculo_cotizacion", "INTEGER NOT NULL DEFAULT 1"),
]

COLUMNAS_ITEMS = [
    ("cantidad_requerida", "TEXT"),
    ("unidad_requerida", "TEXT"),
    ("contenido_presentacion", "TEXT"),
    ("unidad_presentacion", "TEXT"),
    ("unidad_compra", "TEXT"),
    ("presentacion_divisible", "INTEGER NOT NULL DEFAULT 0"),
    ("regla_redondeo", "TEXT"),
    ("unidades_compra", "TEXT"),
    ("unidades_compra_override", "TEXT"),
    ("cobertura_compra", "TEXT"),
    ("sobrante_estimado", "TEXT"),
    ("precio_presentacion_crc", "INTEGER"),
    ("subtotal_cotizado_crc", "INTEGER"),
    ("estado_calculo", "TEXT"),
    ("motivo_revision", "TEXT"),
    ("version_calculo", "INTEGER"),
    ("origen_presentacion", "TEXT"),
    ("override_reconocido", "INTEGER NOT NULL DEFAULT 0"),
    ("cobertura_insuficiente", "INTEGER NOT NULL DEFAULT 0"),
]

COLUMNAS_PRESUPUESTO = [
    ("version_calculo", "INTEGER"),
]

# La tabla es la única fuente de factores. Las filas de identidad también
# centralizan alias y unidades de conteo; no implican conversiones entre
# tipos de empaque distintos.
UNIDADES = [
    ("m", "m", "1", "longitud"),
    ("metro", "m", "1", "longitud"),
    ("metros", "m", "1", "longitud"),
    ("m2", "m²", "1", "area"),
    ("metro cuadrado", "m²", "1", "area"),
    ("metros cuadrados", "m²", "1", "area"),
    ("l", "L", "1", "volumen"),
    ("litro", "L", "1", "volumen"),
    ("litros", "L", "1", "volumen"),
    ("galon", "L", "3.785411784", "volumen"),
    ("galones", "L", "3.785411784", "volumen"),
    ("kg", "kg", "1", "masa"),
    ("kilogramo", "kg", "1", "masa"),
    ("kilogramos", "kg", "1", "masa"),
    ("lb", "kg", "0.45359237", "masa"),
    ("lbs", "kg", "0.45359237", "masa"),
    ("libra", "kg", "0.45359237", "masa"),
    ("libras", "kg", "0.45359237", "masa"),
    ("unidad", "count:unidad", "1", "conteo"),
    ("count unidad", "count:unidad", "1", "conteo"),
    ("unidades", "count:unidad", "1", "conteo"),
    ("und", "count:unidad", "1", "conteo"),
    ("pieza", "count:unidad", "1", "conteo"),
    ("piezas", "count:unidad", "1", "conteo"),
    ("par", "count:par", "1", "conteo"),
    ("count par", "count:par", "1", "conteo"),
    ("pares", "count:par", "1", "conteo"),
    ("caja", "count:caja", "1", "conteo"),
    ("count caja", "count:caja", "1", "conteo"),
    ("cajas", "count:caja", "1", "conteo"),
    ("saco", "count:saco", "1", "conteo"),
    ("count saco", "count:saco", "1", "conteo"),
    ("sacos", "count:saco", "1", "conteo"),
    ("rollo", "count:rollo", "1", "conteo"),
    ("count rollo", "count:rollo", "1", "conteo"),
    ("rollos", "count:rollo", "1", "conteo"),
    ("envase", "count:envase", "1", "conteo"),
    ("count envase", "count:envase", "1", "conteo"),
    ("envases", "count:envase", "1", "conteo"),
    ("paquete", "count:paquete", "1", "conteo"),
    ("count paquete", "count:paquete", "1", "conteo"),
    ("paquetes", "count:paquete", "1", "conteo"),
    ("bolsa", "count:bolsa", "1", "conteo"),
    ("count bolsa", "count:bolsa", "1", "conteo"),
    ("bolsas", "count:bolsa", "1", "conteo"),
]


def _columnas(cursor, tabla):
    return {fila[1] for fila in cursor.execute(f"PRAGMA table_info({tabla})").fetchall()}


def _agregar_faltantes(cursor, tabla, columnas):
    existentes = _columnas(cursor, tabla)
    if not existentes:
        return
    for nombre, definicion in columnas:
        if nombre not in existentes:
            cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {definicion}")
            print(f"  + {tabla}.{nombre}")


def main():
    conexion = conectar()
    cursor = conexion.cursor()
    _agregar_faltantes(cursor, "proyectos", COLUMNAS_PROYECTOS)
    _agregar_faltantes(cursor, "items_proyecto", COLUMNAS_ITEMS)
    _agregar_faltantes(cursor, "presupuesto_congelado", COLUMNAS_PRESUPUESTO)

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversiones_unidad (
            unidad_origen TEXT PRIMARY KEY,
            unidad_canonica TEXT NOT NULL,
            factor_a_canonica TEXT NOT NULL,
            dimension TEXT NOT NULL,
            activa INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    cursor.executemany(
        """
        INSERT INTO conversiones_unidad (
            unidad_origen, unidad_canonica, factor_a_canonica, dimension, activa
        ) VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(unidad_origen) DO UPDATE SET
            unidad_canonica = excluded.unidad_canonica,
            factor_a_canonica = excluded.factor_a_canonica,
            dimension = excluded.dimension,
            activa = 1
        """,
        UNIDADES,
    )

    conexion.commit()
    conteos = cursor.execute(
        """
        SELECT
            COUNT(*) AS proyectos_legacy,
            (SELECT COUNT(*) FROM items_proyecto WHERE estado_calculo IS NULL) AS items_sin_clasificar
        FROM proyectos WHERE version_calculo_cotizacion = 1
        """
    ).fetchone()
    conexion.close()
    print(
        "✅ Cálculo de compra listo; "
        f"proyectos_legacy={conteos[0]}, items_sin_clasificar={conteos[1]}"
    )


if __name__ == "__main__":
    main()
