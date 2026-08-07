"""Migración aditiva: tabla de líneas base de presupuesto (ver
CONTROL_DE_COSTOS.md / ARQUITECTURA_PLATAFORMA_INTEGRAL.md, módulo 7).

Una fila por cada vez que un proyecto "congela" su cotización actual como
línea base -- nunca se sobrescribe ni se actualiza una fila existente
(mismo criterio que `eventos`: es un registro histórico, no un estado
mutable). La línea base ACTIVA es, por diseño, la más reciente de un
proyecto -- si el alcance cambia y se vuelve a congelar, la anterior no
se borra, solo deja de ser la que se usa para comparar (permite auditar
"qué se aprobó, cuándo" más adelante sin rediseñar nada).

`snapshot_json` guarda las partidas completas con subtotal (mismo formato
que ya devuelve `_calcular_cotizacion()["partidas"]`) -- mismo patrón que
`proyectos.plano_analisis` (columna TEXT con JSON serializado a mano, sin
tipo JSON nativo, ver api/repositorio_proyectos.py).

Sin llave foránea a `proyectos` a propósito -- mismo criterio que
`eventos` (database/agregar_eventos.py): un registro de auditoría no
debería depender de que el proyecto que lo originó siga existiendo.

No se toca ninguna tabla existente. Reejecutar este script no debe fallar
ni duplicar nada."""

from db import conectar


def main():
    conexion = conectar()
    cursor = conexion.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS presupuesto_congelado (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proyecto_id INTEGER NOT NULL,
            fecha_creacion TEXT NOT NULL,
            subtotal_materiales REAL NOT NULL,
            indirectos REAL NOT NULL,
            imprevistos REAL NOT NULL,
            margen REAL NOT NULL,
            total_final REAL NOT NULL,
            snapshot_json TEXT NOT NULL
        )
    """)

    # Cada consulta de control de costos busca "la línea base más reciente
    # de este proyecto" -- este índice cubre exactamente ese acceso.
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_presupuesto_congelado_proyecto "
        "ON presupuesto_congelado (proyecto_id, fecha_creacion)"
    )

    conexion.commit()
    conexion.close()

    print("✅ Tabla de presupuesto congelado (Control de Costos) lista.")


if __name__ == "__main__":
    main()
