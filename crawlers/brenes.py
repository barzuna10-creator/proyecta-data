import html
from datetime import datetime

import requests

from crawlers.comun import guardar_productos


API_URL = "https://ferreteriabrenes.com/wp-json/wc/store/v1/products"

TAMANO_PAGINA = 100

CATEGORIAS = {
    "Cerrajeria": 954,
    "Construccion": 877,
    "Electrico": 958,
    "Ferreteria": 959,
    "General": 15,
    "Griferia": 956,
    "Herramientas": 27,
    "Hogar": 22,
    "Iluminacion": 960,
    "Jardineria": 964,
    "Pinturas": 25,
    "Salud Ocupacional": 18,
    "Varios": 130,
}


def descargar_categoria(categoria_id):
    pagina = 1
    productos_totales = []

    while True:
        response = requests.get(
            API_URL,
            params={
                "category": categoria_id,
                "per_page": TAMANO_PAGINA,
                "page": pagina,
            },
            timeout=30,
        )

        response.raise_for_status()

        productos = response.json()

        if not productos:
            break

        productos_totales.extend(productos)

        print(
            f"Página {pagina}: "
            f"{len(productos)} productos"
        )

        total_paginas = int(
            response.headers.get("X-WP-TotalPages", pagina)
        )

        if pagina >= total_paginas:
            break

        pagina += 1

    return productos_totales


def normalizar_producto(producto, categoria_nombre):
    categorias = producto.get("categories") or []
    subcategoria = categorias[-1]["name"] if categorias else None

    marcas = producto.get("brands") or []
    marca = marcas[0]["name"] if marcas else None

    imagenes = producto.get("images") or []
    url_imagen = imagenes[0]["src"] if imagenes else None

    precios = producto.get("prices") or {}
    precio = precios.get("price")
    unidad_menor = int(precios.get("currency_minor_unit", 2))

    return {
        "proveedor": "Ferretería Brenes",
        "id_proveedor": str(producto.get("id")),
        "sku": producto.get("sku"),
        "nombre": html.unescape(producto.get("name") or ""),
        "marca": marca,
        "categoria": categoria_nombre,
        "subcategoria": subcategoria,
        "precio": (
            round(float(precio) / (10 ** unidad_menor))
            if precio is not None
            else None
        ),
        "iva": None,
        "cabys": None,
        "descripcion": None,
        "url_imagen": url_imagen,
        "url_producto": producto.get("permalink"),
        "compra_online": 1 if producto.get("is_in_stock") else 0,
        "fecha_actualizacion": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }


def actualizar():
    print("\n=== ACTUALIZANDO FERRETERÍA BRENES ===\n")

    productos_normalizados = []

    for nombre_categoria, categoria_id in CATEGORIAS.items():
        print(f"Descargando {nombre_categoria}...")

        try:
            productos = descargar_categoria(categoria_id)

        except requests.RequestException as error:
            print(
                f"Error descargando {nombre_categoria}: "
                f"{error}"
            )
            continue

        for producto in productos:
            producto_normalizado = normalizar_producto(
                producto,
                nombre_categoria,
            )

            productos_normalizados.append(
                producto_normalizado
            )

        print(
            f"{len(productos)} productos descargados.\n"
        )

    cantidad = guardar_productos(
        productos_normalizados
    )

    print(
        f"✅ Ferretería Brenes actualizada: "
        f"{cantidad} productos."
    )

    return cantidad
