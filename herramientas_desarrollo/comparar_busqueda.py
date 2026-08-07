"""Etapa 1 - script de comparación: buscador actual (LIKE + precio ASC) vs.
motor nuevo (FTS5 + bm25 ponderado, sin re-ranking en Python todavía).

No modifica /buscar. Solo imprime un reporte para revisión manual.
"""

from db import conectar
from busqueda import buscar_fts

TERMINOS = [
    # dados explícitamente por el usuario
    "escalera",
    "pintura",
    "cemento",
    "taladro",
    "cable thhn",
    "broca",
    # una sola palabra, alta frecuencia
    "martillo",
    "tornillo",
    "llave",
    "sierra",
    "disco",
    "lija",
    "manguera",
    "destornillador",
    "cerradura",
    "candado",
    "tuerca",
    "perno",
    "silicon",
    "alambre",
    "varilla",
    "angulo",
    "teja",
    "block",
    # multi-palabra / con medidas (el caso que más falla hoy)
    "escalera 3 metros",
    "taladro 1/2",
    "cinta metrica 5 metros",
    "tubo pvc 1/2 pulgada",
    "varilla corrugada 3/8",
    "pintura 1 galon",
    "cable 12 awg",
    "llave de tuerca 1/2 pulgada",
    "broca para concreto 3/8",
    # marcas
    "bosch",
    "truper",
    "stanley",
    "dewalt",
    "ingco",
    # ambiguos / categoría
    "pintura blanca",
    "brocha",
    "cemento gris",
    "cerradura de pomo",
    # casos raros / posible cero resultados
    "martillo neumatico",
    "sierra circular 7 1/4",
]


def buscar_actual(q, limite=50):
    conexion = conectar()
    filas = conexion.execute(
        """
        SELECT nombre, precio, categoria, proveedor
        FROM productos
        WHERE nombre LIKE ?
        ORDER BY precio ASC
        LIMIT ?
        """,
        (f"%{q}%", limite),
    ).fetchall()
    conexion.close()
    return [dict(f) for f in filas]


def main():
    for termino in TERMINOS:
        actuales = buscar_actual(termino)
        nuevos = buscar_fts(termino)

        print("=" * 100)
        print(f'"{termino}"  ->  actual: {len(actuales)} resultados | fts5: {len(nuevos)} resultados')
        print("-" * 100)

        print("ACTUAL (LIKE + precio ASC):")
        for r in actuales[:5]:
            print(f"  ₡{r['precio']:>10,.0f}  {r['nombre'][:70]}")

        print("NUEVO (FTS5 + bm25 ponderado):")
        for r in nuevos[:5]:
            print(f"  ₡{r['precio']:>10,.0f}  {r['nombre'][:70]}")

        print()


if __name__ == "__main__":
    main()
