"""Actualización de Novex -- proceso independiente y lento, mismo motivo
que El Lagar (ver actualizar_ellagar.py): ~767 categorías reales tras
excluir departamentos ajenos a construcción, con 2 segundos de pausa
obligatoria entre peticiones (confirmado con un spike real que Novex, a
diferencia de los demás proveedores, tiene Cloudflare con bot-management
activo -- ver NOVEX_SPIKE.md). Eso solo son ~25+ minutos en pausas antes
de sumar el tiempo de red real, así que se separa de `main.py` por la
misma razón que El Lagar: no bloquear el refresco rápido de EPA, Brenes,
Carbone Store y Construplaza.

Pensado para correr de noche. Esta fase NO configura ningún cron ni
servicio externo -- solo deja el comando listo:

    0 3 * * *  cd /ruta/al/proyecto && /ruta/al/venv/bin/python actualizar_novex.py >> logs/novex_nocturno.log 2>&1

A diferencia de El Lagar, `crawlers/novex.py` todavía no tiene checkpoint
de reanudación -- si el proceso se corta a mitad de camino, la próxima
corrida arranca de cero (aceptable por ahora dado que cada categoría hace
upsert independiente vía guardar_productos(), así que no se pierde lo ya
guardado, solo se tarda en volver a visitarlo)."""

import sys
import time
from datetime import datetime

from crawlers import novex
from busqueda import reconstruir_indice


def main():
    print("=== PROYECTA CR — actualización de Novex (proceso independiente) ===")
    print(f"Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    t0 = time.time()

    try:
        cantidad = novex.actualizar()
    except Exception as error:
        duracion = time.time() - t0
        print(f"\n❌ Novex falló tras {duracion:.1f}s -- {error}")
        print(
            "El catálogo de Novex queda con su última actualización exitosa "
            "-- no se reconstruye el índice de búsqueda con esta corrida fallida."
        )
        sys.exit(1)

    duracion = time.time() - t0
    print(f"\n✅ Novex: {cantidad} productos en {duracion:.1f}s")

    print("\nReconstruyendo índice de búsqueda...")
    t0 = time.time()
    n_indexados = reconstruir_indice()
    print(f"✅ Índice reconstruido: {n_indexados} productos en {time.time() - t0:.2f}s")
    print(f"\nFin: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
