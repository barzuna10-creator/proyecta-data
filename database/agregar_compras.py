"""Migración aditiva: Flujo de Compras (ver COMPRAS.md).

- `items_proyecto` gana el rastro de compra real de cada ítem:
  `cantidad_comprada` (cuánto de `cantidad` ya se compró -- puede ser
  parcial), `monto_comprado` (cuánto costó de verdad, acumulado; NULL
  significa "todavía no hay un monto real registrado, usar cantidad ×
  precio como estimado" -- mismo criterio que ya usan el resto de las
  columnas `_al_agregar`), `fecha_compra` (la más reciente), y
  `comprobante_tipo`/`comprobante_referencia` para la carga manual
  asistida de facturas/comprobantes (ver sección de diseño en
  COMPRAS.md -- todavía sin OCR, solo referencia de texto).

- `ordenes_compra`, tabla nueva: un documento por cada vez que se agrupa
  y "genera" una orden de compra para un proveedor -- mismo patrón que
  `presupuesto_congelado` (evento inmutable, nunca se sobreescribe, la
  vigente es la más reciente por proveedor si se necesita).

No se toca ninguna columna existente. `estado` sigue siendo TEXT libre
(sin CHECK constraint en la base, ver ESTADOS_ITEM en
api/repositorio_proyectos.py para la validación real) -- agregarle el
valor "parcial" no requiere ningún cambio de esquema, solo de código.

Reejecutar este script no debe fallar ni duplicar nada."""

from db import conectar


COLUMNAS_ITEMS_PROYECTO = [
    ("cantidad_comprada", "REAL NOT NULL DEFAULT 0"),
    ("monto_comprado", "REAL"),
    ("fecha_compra", "TEXT"),
    ("comprobante_tipo", "TEXT"),
    ("comprobante_referencia", "TEXT"),
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
    _agregar_columnas_faltantes(cursor, "items_proyecto", COLUMNAS_ITEMS_PROYECTO)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ordenes_compra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proyecto_id INTEGER NOT NULL,
            proveedor TEXT NOT NULL,
            numero TEXT NOT NULL,
            fecha_creacion TEXT NOT NULL,
            monto_total REAL NOT NULL,
            snapshot_json TEXT NOT NULL
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ordenes_compra_proyecto "
        "ON ordenes_compra (proyecto_id, fecha_creacion)"
    )

    conexion.commit()
    conexion.close()

    print("✅ Esquema de Compras listo.")


if __name__ == "__main__":
    main()
