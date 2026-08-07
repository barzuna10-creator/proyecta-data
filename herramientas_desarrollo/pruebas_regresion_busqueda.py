"""Pruebas de regresión para la capa de variantes morfológicas (género/número)
agregada sobre FTS5 en busqueda.py.

Dos cosas se verifican:
1. Los 5 casos objetivo (pintura blanca/blanco/roja/negras, escalera 3 metros)
   ahora encuentran los productos reales que antes se perdían por falta de
   concordancia de género o número entre la consulta y el catálogo.
2. Ninguna de las 44 búsquedas ya validadas en Etapa 1 (database/baseline_busqueda_regresion.json,
   capturada ANTES de este cambio) empeoró: mismo total de resultados y mismo
   top 5, salvo "pintura blanca", que mejora a propósito.

No usa un framework de pruebas — sigue el mismo estilo de script ejecutable
que comparar_busqueda.py. Termina con código de salida distinto de 0 si algo falla.
"""

import json
import sys

from busqueda import buscar_fts

RUTA_BASELINE = "database/baseline_busqueda_regresion.json"

# Términos cuyo resultado se espera que cambie a propósito con este fix.
TERMINOS_QUE_MEJORAN = {"pintura blanca"}

CASOS_OBJETIVO = [
    {
        "consulta": "pintura blanca",
        "minimo_resultados_default": 10,
        "proveedor_afectado": "EPA",
        "minimo_coincidencias_reales": 50,
        "razon": "EPA nombra sus pinturas en masculino ('blanco'); antes del fix el MATCH no encontraba ninguna (0 coincidencias reales sin límite).",
    },
    {
        "consulta": "pintura blanco",
        "minimo_resultados_default": 40,
        "proveedor_afectado": None,
        "minimo_coincidencias_reales": 0,
        "razon": "Consulta ya funcionaba (coincide literal con el catálogo); debe seguir funcionando igual o mejor.",
    },
    {
        "consulta": "pintura roja",
        "minimo_resultados_default": 10,
        "proveedor_afectado": "El Lagar",
        "minimo_coincidencias_reales": 40,
        "razon": "Antes del fix el MATCH solo encontraba 1 producto en todo el catálogo (una bandeja plástica, ni siquiera pintura).",
    },
    {
        "consulta": "pintura negras",
        "minimo_resultados_default": 10,
        "proveedor_afectado": None,
        "minimo_coincidencias_reales": 0,
        "razon": "Antes del fix el MATCH daba 0 coincidencias en todo el catálogo (plural femenino no existía en ningún token indexado).",
    },
    {
        "consulta": "escalera 3 metros",
        "minimo_resultados_default": 5,
        "proveedor_afectado": None,
        "minimo_coincidencias_reales": 0,
        "razon": "Caso ya resuelto en Etapa 1 (sinónimos de unidades); no debe romperse con la nueva capa de variantes morfológicas.",
    },
]

LIMITE_SIN_TOPE = 1000


def evaluar_casos_objetivo():
    fallos = []

    for caso in CASOS_OBJETIVO:
        consulta = caso["consulta"]

        # Lo que un usuario real ve hoy (limite=50 por defecto en /buscar).
        filas_default = buscar_fts(consulta)
        total_default = len(filas_default)

        print(f"\n'{consulta}' -> {total_default} resultados (limite=50, lo que ve el usuario)  ({caso['razon']})")

        if total_default < caso["minimo_resultados_default"]:
            fallos.append(
                f"'{consulta}': se esperaban al menos {caso['minimo_resultados_default']} "
                f"resultados con limite=50, se obtuvieron {total_default}"
            )

        for f in filas_default[:3]:
            print(f"    - {f['nombre']}")

        # Lo que realmente encuentra el MATCH de FTS5 sin el recorte del LIMIT
        # de producción — esto es lo que la capa de variantes controla; el
        # ranking dentro del top 50 es tema de bm25 / Etapa 3, no de este fix.
        if caso["proveedor_afectado"]:
            filas_amplias = buscar_fts(consulta, limite=LIMITE_SIN_TOPE)
            cantidad = sum(1 for f in filas_amplias if f["proveedor"] == caso["proveedor_afectado"])
            print(f"  Coincidencias reales de {caso['proveedor_afectado']} (sin limite de produccion): {cantidad}")
            if cantidad < caso["minimo_coincidencias_reales"]:
                fallos.append(
                    f"'{consulta}': el MATCH debería encontrar al menos "
                    f"{caso['minimo_coincidencias_reales']} productos de "
                    f"{caso['proveedor_afectado']}, encontró {cantidad}"
                )
            if cantidad > 0 and sum(1 for f in filas_default if f["proveedor"] == caso["proveedor_afectado"]) == 0:
                print(
                    f"  NOTA: {caso['proveedor_afectado']} tiene coincidencias reales pero "
                    f"ninguna entra en el top 50 por bm25 — esto es ranking, no tokenización, "
                    f"y queda para Etapa 3."
                )

    return fallos


def evaluar_no_regresion():
    with open(RUTA_BASELINE, encoding="utf-8") as fh:
        baseline = json.load(fh)

    fallos = []
    sin_cambio = 0
    mejoras_esperadas = 0

    for termino, esperado in baseline.items():
        filas = buscar_fts(termino)
        total = len(filas)
        top5 = [f["nombre"] for f in filas[:5]]

        if total == esperado["total"] and top5 == esperado["top5"]:
            sin_cambio += 1
            continue

        if termino in TERMINOS_QUE_MEJORAN and total >= esperado["total"]:
            mejoras_esperadas += 1
            continue

        # Cualquier otro cambio (sobre todo una baja en el total) es una regresión.
        if total < esperado["total"]:
            fallos.append(
                f"'{termino}': REGRESIÓN — antes {esperado['total']} resultados, "
                f"ahora {total}"
            )
        else:
            fallos.append(
                f"'{termino}': cambio inesperado no declarado en "
                f"TERMINOS_QUE_MEJORAN — antes top5={esperado['top5']}, "
                f"ahora top5={top5}"
            )

    print(
        f"\nRegresión sobre {len(baseline)} términos de Etapa 1: "
        f"{sin_cambio} sin cambio, {mejoras_esperadas} mejoraron como se esperaba, "
        f"{len(fallos)} con problemas"
    )

    return fallos


def main():
    print("=" * 100)
    print("CASOS OBJETIVO (deben encontrar resultados que antes se perdían)")
    print("=" * 100)
    fallos = evaluar_casos_objetivo()

    print()
    print("=" * 100)
    print("NO REGRESIÓN (búsquedas ya resueltas en Etapa 1)")
    print("=" * 100)
    fallos += evaluar_no_regresion()

    print()
    print("=" * 100)
    if fallos:
        print(f"FALLÓ: {len(fallos)} problema(s)")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("TODAS LAS PRUEBAS PASARON")


if __name__ == "__main__":
    main()
