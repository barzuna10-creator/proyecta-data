"""Ferreterías El Mar (ver ESTRATEGIA_EXPANSION_PROVEEDORES.md, candidato
#5, "el técnicamente más limpio de los 23 investigados"): WooCommerce
Store API pública, sin autenticación, sin Cloudflare ni reCAPTCHA
detectados. Confirmado en vivo antes de escribir este archivo:
`GET https://ferreteriaelmar.com/wp-json/wc/store/v1/products` devuelve
JSON estructurado real (mismo patrón que crawlers/brenes.py).

Catálogo real pequeño (16 productos, confirmado por X-WP-Total) --
negocio de 2 sucursales, no una cadena grande. A diferencia de Brenes,
CADA producto sí trae su propia lista `categories` en el listado general
(verificado en vivo), así que acá no hace falta pedir por categoría por
separado -- se pagina el catálogo completo de una vez y la categoría se
lee del producto mismo. Con un catálogo tan chico no hay categorías
irrelevantes que filtrar (ferretería/acabados/techos, todo relevante)."""

import html
from datetime import datetime

import requests

from crawlers.comun import (
    descargar_paginado,
    ejecutar_actualizacion,
    limpiar_html,
    pedir_con_reintentos,
    serializar_imagenes,
)

API_URL = "https://ferreteriaelmar.com/wp-json/wc/store/v1/products"

TAMANO_PAGINA = 100


def descargar_productos():
    def pedir_pagina(pagina):
        response = pedir_con_reintentos(
            requests.get,
            API_URL,
            params={"per_page": TAMANO_PAGINA, "page": pagina},
            timeout=30,
        )

        response.raise_for_status()

        productos = response.json()
        total_paginas = int(response.headers.get("X-WP-TotalPages", pagina))

        return productos, pagina >= total_paginas

    return descargar_paginado(pedir_pagina)


def normalizar_producto(producto):
    categorias = producto.get("categories") or []
    # La primera es la más general (ej. "Acabados"), la última la más
    # específica (ej. "Inodoros") -- verificado contra el orden real que
    # devuelve la API (ver docstring del módulo). Si solo hay una, sirve
    # igual para ambos campos sin inventar una subcategoría que no existe.
    categoria = categorias[0]["name"] if categorias else "General"
    subcategoria = categorias[-1]["name"] if len(categorias) > 1 else None

    imagenes = producto.get("images") or []
    url_imagen = imagenes[0]["src"] if imagenes else None
    imagenes_adicionales = [img["src"] for img in imagenes[1:]] if len(imagenes) > 1 else None

    precios = producto.get("prices") or {}
    precio = precios.get("price")
    unidad_menor = int(precios.get("currency_minor_unit", 2))

    return {
        "proveedor": "Ferreterías El Mar",
        "id_proveedor": str(producto.get("id")),
        "sku": producto.get("sku") or None,
        "nombre": html.unescape((producto.get("name") or "").strip()),
        "marca": None,  # sin campo de marca estructurado en esta tienda -- no se inventa
        "categoria": categoria,
        "subcategoria": subcategoria,
        "precio": (
            round(float(precio) / (10 ** unidad_menor))
            if precio is not None
            else None
        ),
        "iva": None,
        "cabys": None,
        "descripcion": limpiar_html(producto.get("short_description")),
        "url_imagen": url_imagen,
        "url_producto": producto.get("permalink"),
        "compra_online": 1 if producto.get("is_in_stock") else 0,
        "fecha_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "imagenes_adicionales": serializar_imagenes(imagenes_adicionales),
    }


def actualizar():
    return ejecutar_actualizacion(
        "Ferreterías El Mar",
        lambda: [normalizar_producto(p) for p in descargar_productos()],
        forma_verbo="actualizada",
    )
