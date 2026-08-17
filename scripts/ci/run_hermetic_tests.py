"""Ejecuta la suite del backend sin depender del catálogo rastreado.

Se excluye exactamente este contrato externo:

``test_sistemas_constructivos.PruebaTerminosDeBusquedaContraElCatalogoReal.``
``test_todos_los_materiales_de_todos_los_sistemas_devuelven_resultados``

Ese contrato consulta ``database/proyecta.db`` y, por definición, depende del
dataset privado versionado. CI no materializa esa base. El runner falla si el
contrato no aparece exactamente una vez, para detectar renombres o exclusiones
accidentales en vez de ampliar silenciosamente el alcance omitido.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


RAIZ_REPO = Path(__file__).resolve().parents[2]
DIRECTORIO_TESTS = RAIZ_REPO / "tests"
BASE_RASTREADA = RAIZ_REPO / "database" / "proyecta.db"
CONTRATO_EXTERNO = (
    "test_sistemas_constructivos.PruebaTerminosDeBusquedaContraElCatalogoReal."
    "test_todos_los_materiales_de_todos_los_sistemas_devuelven_resultados"
)


def _verificar_aislamiento(etapa: str) -> Path:
    if BASE_RASTREADA.exists():
        raise RuntimeError(
            f"{etapa}: database/proyecta.db existe; la suite hermética se cancela"
        )

    valor_database_path = os.environ.get("DATABASE_PATH")
    if not valor_database_path:
        raise RuntimeError(f"{etapa}: DATABASE_PATH no está definido")

    base_temporal = Path(valor_database_path).expanduser().resolve()
    if base_temporal == BASE_RASTREADA.resolve():
        raise RuntimeError(
            f"{etapa}: DATABASE_PATH apunta a database/proyecta.db"
        )

    base_temporal.parent.mkdir(parents=True, exist_ok=True)
    return base_temporal


def _filtrar_contrato_externo(
    suite: unittest.TestSuite,
) -> tuple[unittest.TestSuite, int]:
    filtrada = unittest.TestSuite()
    coincidencias = 0

    for prueba in suite:
        if isinstance(prueba, unittest.TestSuite):
            sub_suite, sub_coincidencias = _filtrar_contrato_externo(prueba)
            coincidencias += sub_coincidencias
            if sub_suite.countTestCases():
                filtrada.addTest(sub_suite)
        elif prueba.id() == CONTRATO_EXTERNO:
            coincidencias += 1
        else:
            filtrada.addTest(prueba)

    return filtrada, coincidencias


def main() -> int:
    try:
        _verificar_aislamiento("antes del discovery")
        suite_descubierta = unittest.TestLoader().discover(
            start_dir=str(DIRECTORIO_TESTS),
            pattern="test_*.py",
        )
        _verificar_aislamiento("después del discovery")
    except Exception as exc:
        print(f"ERROR de aislamiento: {exc}", file=sys.stderr)
        return 1

    suite, coincidencias = _filtrar_contrato_externo(suite_descubierta)
    if coincidencias != 1:
        print(
            "ERROR: se esperaba exactamente una coincidencia del contrato externo "
            f"{CONTRATO_EXTERNO!r}, pero se encontraron {coincidencias}",
            file=sys.stderr,
        )
        return 1

    print(f"OMITIDO explícitamente (contrato externo del catálogo): {CONTRATO_EXTERNO}")
    resultado = unittest.TextTestRunner(verbosity=2).run(suite)

    try:
        _verificar_aislamiento("después de ejecutar la suite")
    except Exception as exc:
        print(f"ERROR de aislamiento: {exc}", file=sys.stderr)
        return 1

    return 0 if resultado.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
