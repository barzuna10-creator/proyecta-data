"""Actualización rápida del catálogo: EPA, Ferretería Brenes, Carbone Store
y Construplaza (todos vía llamadas en lote -- Construplaza es el más
liviano de los cuatro, ~22 páginas para todo su catálogo vía la API
pública de Algolia). El Lagar queda fuera a propósito -- necesita una
llamada HTTP por producto y tarda ~50 minutos; se actualiza por separado
con `actualizar_ellagar.py`, pensado para correr de noche (ver ese
archivo)."""

import sys
import time
from datetime import datetime

from crawlers import brenes, carbone, construplaza, epa
from busqueda import reconstruir_indice

PROVEEDORES = [epa, brenes, carbone, construplaza]


def _actualizar_proveedores(proveedores):
    """Corre `actualizar()` de cada proveedor, sin dejar que la falla de
    uno detenga a los demás. Cada proveedor ya guarda sus propios datos con
    upsert (nunca DELETE) y solo hace commit al terminar su descarga --
    si uno falla, los datos que ya tenía guardados de la corrida anterior
    quedan intactos, no se pierden ni se corrompen."""

    resultados = []

    for proveedor in proveedores:
        nombre = proveedor.__name__.rsplit(".", 1)[-1]
        t0 = time.time()

        try:
            cantidad = proveedor.actualizar()
            duracion = time.time() - t0
            resultados.append(
                {"proveedor": nombre, "ok": True, "cantidad": cantidad, "duracion": duracion}
            )
            print(f"✅ {nombre}: {cantidad} productos en {duracion:.1f}s\n")
        except Exception as error:
            duracion = time.time() - t0
            resultados.append(
                {"proveedor": nombre, "ok": False, "duracion": duracion, "error": str(error)}
            )
            print(f"❌ {nombre}: falló tras {duracion:.1f}s -- {error}\n")

    return resultados


def _imprimir_resumen(resultados):
    print("=== Resumen ===")
    for r in resultados:
        if r["ok"]:
            print(f"  OK     {r['proveedor']:<20} {r['cantidad']:>6} productos  {r['duracion']:>7.1f}s")
        else:
            print(f"  ERROR  {r['proveedor']:<20} {'':>6}            {r['duracion']:>7.1f}s  -- {r['error']}")


def main():
    print("=== PROYECTA CR — actualización de catálogo (EPA, Brenes, Carbone) ===")
    print(f"Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    resultados = _actualizar_proveedores(PROVEEDORES)
    exitosos = [r for r in resultados if r["ok"]]

    _imprimir_resumen(resultados)

    if not exitosos:
        print("\n❌ Ningún proveedor se actualizó -- no se reconstruye el índice de búsqueda.")
        sys.exit(1)

    print(f"\nReconstruyendo índice de búsqueda ({len(exitosos)}/{len(PROVEEDORES)} proveedores actualizados)...")
    t0 = time.time()
    n_indexados = reconstruir_indice()
    print(f"✅ Índice reconstruido: {n_indexados} productos en {time.time() - t0:.2f}s")
    print(f"\nFin: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if len(exitosos) < len(PROVEEDORES):
        print(
            f"\n⚠️  {len(PROVEEDORES) - len(exitosos)} proveedor(es) fallaron esta corrida "
            "-- el índice sí se reconstruyó con los datos disponibles, pero conviene revisar "
            "el error y volver a correr para esos proveedores."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
