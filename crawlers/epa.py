from datetime import datetime

import requests

from crawlers.comun import (
    guardar_productos,
    limpiar_html,
    pedir_con_reintentos,
    serializar_imagenes,
)


API_URL = "https://cr.epaenlinea.com/graphql"
SITIO = "https://cr.epaenlinea.com"

TAMANO_PAGINA = 500

CATEGORIAS = {
    "Automotriz": 13829,
    "Baños": 13832,
    "Cocinas": 13835,
    "Decoracion": 13838,
    "Construccion": 13841,
    "Electrodomesticos": 13844,
    "Electricidad": 13847,
    "Exteriores": 13850,
    "Ferreteria y cerrajeria": 13853,
    "Herramientas": 13856,
    "Lamparas": 13859,
    "Limpieza": 13862,
    "Maderas y puertas": 13865,
    "Muebles y organizacion": 13868,
    "Pinturas": 13871,
    "Pisos": 13874,
    "Plomeria": 13877,
    "Seguridad": 13880,
}

CONSULTA_PRODUCTOS = """
query ($categoriaId: String!, $pagina: Int!, $tamanoPagina: Int!) {
    products(
        filter: { category_id: { eq: $categoriaId } }
        pageSize: $tamanoPagina
        currentPage: $pagina
    ) {
        total_count
        page_info {
            total_pages
        }
        items {
            sku
            name
            url_key
            stock_status
            description {
                html
            }
            media_gallery {
                url
            }
            ... on SimpleProduct {
                weight
            }
            price_range {
                minimum_price {
                    final_price {
                        value
                    }
                }
            }
            image {
                url
            }
            categories {
                name
            }
        }
    }
}
"""


def construir_url_producto(url_key):
    return f"{SITIO}/{url_key}.html"


def descargar_categoria(categoria_id):
    pagina = 1
    productos_totales = []

    while True:
        payload = {
            "query": CONSULTA_PRODUCTOS,
            "variables": {
                "categoriaId": str(categoria_id),
                "pagina": pagina,
                "tamanoPagina": TAMANO_PAGINA,
            },
        }

        response = pedir_con_reintentos(
            requests.post,
            API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )

        response.raise_for_status()

        respuesta = response.json()

        if respuesta.get("errors"):
            raise requests.RequestException(respuesta["errors"])

        data = respuesta.get("data", {}).get("products") or {}
        productos = data.get("items") or []

        if not productos:
            break

        productos_totales.extend(productos)

        print(
            f"Página {pagina}: "
            f"{len(productos)} productos"
        )

        total_paginas = data.get("page_info", {}).get("total_pages", pagina)

        if pagina >= total_paginas:
            break

        pagina += 1

    return productos_totales


def normalizar_producto(producto, categoria_nombre):
    categorias = producto.get("categories") or []
    subcategoria = categorias[-1]["name"] if categorias else None

    precio_final = (
        producto.get("price_range", {})
        .get("minimum_price", {})
        .get("final_price", {})
        .get("value")
    )

    imagen = producto.get("image") or {}
    descripcion_html = (producto.get("description") or {}).get("html")

    # Todas las imágenes de la galería, sin repetir la que ya se usa como
    # url_imagen principal (para no duplicarla en "imágenes adicionales").
    galeria = producto.get("media_gallery") or []
    imagen_principal = imagen.get("url")
    imagenes_extra = [g.get("url") for g in galeria if g.get("url") != imagen_principal]

    return {
        "proveedor": "EPA",
        "id_proveedor": producto.get("sku"),
        "sku": producto.get("sku"),
        "nombre": producto.get("name"),
        "marca": None,
        "categoria": categoria_nombre,
        "subcategoria": subcategoria,
        "precio": round(precio_final) if precio_final is not None else None,
        "iva": None,
        "cabys": None,
        "descripcion": limpiar_html(descripcion_html),
        "url_imagen": imagen_principal,
        "url_producto": construir_url_producto(producto.get("url_key")),
        "compra_online": 1 if producto.get("stock_status") == "IN_STOCK" else 0,
        "fecha_actualizacion": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        # Peso tal como lo devuelve la API de EPA -- no se etiqueta la
        # unidad porque no encontré ningún campo que la confirme
        # explícitamente (ver informe: se infiere "gramos" por la magnitud
        # típica, pero no está confirmado).
        "peso": str(producto.get("weight")) if producto.get("weight") is not None else None,
        "imagenes_adicionales": serializar_imagenes(imagenes_extra),
    }


def actualizar():
    print("\n=== ACTUALIZANDO EPA ===\n")

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
        f"✅ EPA actualizado: "
        f"{cantidad} productos."
    )

    return cantidad
