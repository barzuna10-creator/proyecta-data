"""Actualización de El Lagar -- proceso independiente y lento (~50 minutos,
una llamada HTTP por producto para el detalle). Se separó de `main.py` a
propósito para no bloquear el refresco rápido de los otros 3 proveedores.

Pensado para correr de noche. Esta fase NO configura ningún cron ni
servicio externo -- solo deja el comando listo. Para programarlo más
adelante (ej. cron de macOS/Linux, una tarea programada, o un cron job en
el hosting donde corra el backend):

    0 2 * * *  cd /ruta/al/proyecto && /ruta/al/venv/bin/python actualizar_ellagar.py >> logs/ellagar_nocturno.log 2>&1

Si el proceso se corta a mitad de camino (red, la máquina se suspende),
`crawlers/ellagar.py` deja un checkpoint en `logs/ellagar_checkpoint.json`
-- volver a correr este mismo comando retoma donde quedó en vez de repetir
todo desde cero.
"""

import sys
import time
from datetime import datetime

from crawlers import ellagar
from busqueda import reconstruir_indice


def main():
    print("=== PROYECTA CR — actualización de El Lagar (proceso independiente) ===")
    print(f"Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    t0 = time.time()

    try:
        cantidad = ellagar.actualizar()
    except Exception as error:
        duracion = time.time() - t0
        print(f"\n❌ El Lagar falló tras {duracion:.1f}s -- {error}")
        print(
            "El catálogo de El Lagar queda con su última actualización exitosa "
            "-- no se reconstruye el índice de búsqueda con esta corrida fallida."
        )
        sys.exit(1)

    duracion = time.time() - t0
    print(f"\n✅ El Lagar: {cantidad} productos en {duracion:.1f}s")

    print("\nReconstruyendo índice de búsqueda...")
    t0 = time.time()
    n_indexados = reconstruir_indice()
    print(f"✅ Índice reconstruido: {n_indexados} productos en {time.time() - t0:.2f}s")
    print(f"\nFin: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
