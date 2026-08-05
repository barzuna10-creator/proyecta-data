"""Respaldo de database/proyecta.db (ver PRODUCTION_READINESS_REVIEW.md,
hallazgo B1: "no existe ningún mecanismo de respaldo en todo el repo" --
antes de este script, la única copia de seguridad que existió jamás fue
una copia manual de un desarrollador antes de correr una migración de
prueba, en un directorio de sesión efímero).

Esto NO resuelve el hallazgo B2 (durabilidad de los datos de producción a
través de un redeploy, que depende de qué plataforma de hosting se use y
cómo esté configurada -- algo que este repo no controla). Es la salvaguarda
mínima que sí cabe dentro de "corregir sin agregar funcionalidades": un
respaldo local, sin dependencias nuevas, para que exista *algún* camino de
recuperación mientras se resuelve el respaldo real de producción.

Usa `sqlite3.Connection.backup()` (equivalente al comando `.backup` de la
CLI de SQLite) en vez de copiar el archivo con `shutil.copy` -- una copia
de archivo plano puede capturar la base a mitad de una escritura si el
proceso de la API sigue corriendo (los archivos -wal/-shm no se copian con
ella); `.backup()` es seguro para copiar una base "en caliente", sin
detener nada.

Los respaldos se guardan en `database/respaldos/`, con timestamp en el
nombre, y NUNCA se versionan en git (ver .gitignore) -- versionar los
respaldos ahí sería repetir exactamente el mismo problema que tiene
database/proyecta.db (hallazgo B3), solo que multiplicado por cada
respaldo.

Uso:
    PYTHONPATH=. .venv/bin/python3 database/respaldar_db.py
    PYTHONPATH=. .venv/bin/python3 database/respaldar_db.py --mantener 10

Para automatizar (cron, launchd, o el scheduler de la plataforma de
despliegue): correr este comando periódicamente. Este script no configura
ningún cron por sí mismo -- ver DEPLOYMENT.md.
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from db import BASE_DATOS

DIRECTORIO_RESPALDOS = Path("database/respaldos")


def respaldar(mantener=20):
    origen = Path(BASE_DATOS)
    if not origen.exists():
        print(f"❌ No existe {origen} -- nada que respaldar.")
        return 1

    DIRECTORIO_RESPALDOS.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = DIRECTORIO_RESPALDOS / f"proyecta_{timestamp}.db"

    conexion_origen = sqlite3.connect(str(origen))
    conexion_destino = sqlite3.connect(str(destino))
    with conexion_destino:
        conexion_origen.backup(conexion_destino)
    conexion_origen.close()
    conexion_destino.close()

    tamano_mb = destino.stat().st_size / (1024 * 1024)
    print(f"✅ Respaldo creado: {destino} ({tamano_mb:.1f} MB)")

    _purgar_respaldos_viejos(mantener)
    return 0


def _purgar_respaldos_viejos(mantener):
    respaldos = sorted(DIRECTORIO_RESPALDOS.glob("proyecta_*.db"))
    de_mas = respaldos[:-mantener] if mantener > 0 else []
    for respaldo in de_mas:
        respaldo.unlink()
        print(f"  - se borró el respaldo viejo {respaldo.name} (se mantienen los últimos {mantener})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mantener", type=int, default=20,
        help="cuántos respaldos recientes conservar (0 = no purgar ninguno). Por defecto: 20.",
    )
    args = parser.parse_args()
    sys.exit(respaldar(mantener=args.mantener))
