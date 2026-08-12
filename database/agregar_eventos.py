"""Migración aditiva: tabla de eventos, la base de instrumentación para
que Proyecta aprenda de lo que hacen los usuarios (ver
ARQUITECTURA_RECOMENDACION_V2.md, Fase 0 -- "instrumentación de
decisiones" es el prerequisito de todo lo demás: sin datos no hay
aprendizaje posible).

Una sola tabla, de propósito general (no una tabla por tipo de evento):
cada fila es UN evento -- qué pasó, sobre qué producto/proyecto/usuario,
cuándo (ver eventos.py para los tipos concretos que hoy se registran:
item_agregado, seleccion_aceptada, seleccion_reemplazada,
seleccion_eliminada, item_eliminado). Deliberadamente SIN llaves foráneas
hacia items_proyecto/proyectos -- es un registro histórico de auditoría,
tiene que sobrevivir a que el ítem o el proyecto se borren después
(reemplazar/eliminar un ítem, o borrar el proyecto entero, no debe borrar
la evidencia de qué pasó). Todo lo necesario para reconstruir el
contexto de un evento se guarda DENORMALIZADO en la fila misma
(proveedor, categoría, confianza...), no vía JOIN a una fila que puede
ya no existir.

No se toca ninguna tabla existente. Reejecutar este script no debe
fallar ni duplicar nada."""

from db import conectar


def main(conexion=None):
    propia = conexion is None
    if propia:
        conexion = conectar()
    cursor = conexion.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            usuario_id TEXT,
            proyecto_id INTEGER,
            item_id INTEGER,
            proveedor TEXT,
            id_proveedor TEXT,
            proveedor_anterior TEXT,
            id_proveedor_anterior TEXT,
            categoria TEXT,
            origen TEXT,
            confianza_match TEXT,
            texto_material TEXT,
            tiempo_hasta_decision_segundos REAL,
            datos_extra TEXT,
            fecha_creacion TEXT NOT NULL
        )
    """)

    # Cada dashboard filtra por tipo y casi siempre acota por fecha,
    # proyecto o categoría -- estos índices cubren esos accesos sin
    # escanear la tabla completa a medida que crece.
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_eventos_tipo ON eventos (tipo)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_eventos_proyecto ON eventos (proyecto_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_eventos_fecha ON eventos (fecha_creacion)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_eventos_categoria ON eventos (categoria)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_eventos_texto_material ON eventos (texto_material)")

    if propia:
        conexion.commit()
        conexion.close()

    print("✅ Tabla de eventos lista.")


if __name__ == "__main__":
    main()
