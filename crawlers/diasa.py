"""Grupo Diasa / Incesa Standard (ver ESTRATEGIA_EXPANSION_PROVEEDORES.md,
candidato #2, "el de mejor relación esfuerzo/resultado de los 23"):
WooCommerce Store API pública, mismo patrón técnico que
crawlers/brenes.py. Suma una categoría que hoy el catálogo no cubría
bien -- acabados (loza sanitaria, grifería, porcelanato) -- y no solo
más volumen de lo mismo.

Confirmado en vivo antes de escribir este archivo (no es la estimación
de 177 productos del documento de estrategia -- el catálogo real creció):
`GET https://grupodiasa.co.cr/wp-json/wc/store/v1/products` expone
X-WP-Total=1318 productos en total. A diferencia de El Mar, el listado
general NO trae el campo `categories` poblado por producto (verificado:
siempre `[]`) -- la única forma de saber la categoría real de un producto
es pedirlo filtrado por `?category={id}`, igual que ya hace Brenes.

Filtro de categorías (auditoría real, no adivinada -- ver el conteo de
cada id abajo, confirmado contra /products/categories y contra
`?category={id}` directamente):

- 58 "Acabados" (551 productos) -- loza sanitaria, grifería, porcelanato,
  azulejos, cerámica, bañeras, duchas, fachaletas. El corazón del
  catálogo, 100% relevante para un ingeniero civil.
- 392 "Ferretería" (223) -- cerrajería, herramienta, pinturas
  arquitectónicas, impermeabilizantes, calentadores.
- 626 "Hogar" (170) -- ~94% de esto (160 de 170) es en realidad
  "Iluminación" (bombillería, lámparas, apliques, faroles), su categoría
  hija más grande por lejos -- se conserva bajo la etiqueta "Iluminación"
  en vez de "Hogar" (más útil y más honesto sobre qué es en realidad),
  aceptando que un puñado de productos de decoración de "Hogar" que no
  son iluminación quedan igual incluidos -- ruido menor, documentado,
  mismo criterio que ya usa crawlers/construplaza.py para su propio
  filtro de relevancia.

Deliberadamente AFUERA (categorías top-level auditadas y descartadas por
ser mobiliario/decoración/electrodomésticos, no material de
construcción): Escritorios, Muebles de Baño, Plantillas, Plantas
Artificiales, Horno, Aroma, Deshumidificador, Mesa, Bancos, Muebles de
Cocina, Armario, Cesta, Cocina, Difusor, Extractores (ambiguo -- podría
ser ventilación de obra, pero en este catálogo aparece mezclado con
electrodomésticos de cocina, se prefiere no incluirlo sin poder
confirmarlo caso por caso)."""

import html
from datetime import datetime

import requests

from crawlers.comun import (
    descargar_paginado,
    descargar_y_normalizar_por_categoria,
    ejecutar_actualizacion,
    limpiar_html,
    pedir_con_reintentos,
    serializar_imagenes,
)

API_URL = "https://grupodiasa.co.cr/wp-json/wc/store/v1/products"

TAMANO_PAGINA = 100

# nombre_categoria (el que se guarda en productos.categoria) -> id real de
# Diasa. Categorías padre (parent=0 o casi) elegidas a propósito -- pedir
# categoría por categoría hijo por separado duplicaría productos (un
# mismo producto puede estar tanto en el padre como en el hijo) sin
# ganar nada, porque el conteo de un padre YA incluye a sus hijos
# (verificado en vivo: `?category=58` devuelve exactamente 551, el mismo
# total que /products/categories reporta para "Acabados").
CATEGORIAS = {
    "Acabados": 58,
    "Ferretería": 392,
    "Iluminación": 626,
}


def descargar_categoria(categoria_id):
    def pedir_pagina(pagina):
        response = pedir_con_reintentos(
            requests.get,
            API_URL,
            params={"category": categoria_id, "per_page": TAMANO_PAGINA, "page": pagina},
            timeout=30,
        )

        response.raise_for_status()

        productos = response.json()
        total_paginas = int(response.headers.get("X-WP-TotalPages", pagina))

        return productos, pagina >= total_paginas

    return descargar_paginado(pedir_pagina)


def normalizar_producto(producto, categoria_nombre):
    imagenes = producto.get("images") or []
    url_imagen = imagenes[0]["src"] if imagenes else None
    imagenes_adicionales = [img["src"] for img in imagenes[1:]] if len(imagenes) > 1 else None

    precios = producto.get("prices") or {}
    precio = precios.get("price")
    unidad_menor = int(precios.get("currency_minor_unit", 2))

    return {
        "proveedor": "Grupo Diasa",
        "id_proveedor": str(producto.get("id")),
        "sku": producto.get("sku") or None,
        "nombre": html.unescape((producto.get("name") or "").strip()),
        "marca": None,  # sin campo de marca estructurado en el listado -- no se inventa
        "categoria": categoria_nombre,
        "subcategoria": None,  # ver docstring del módulo: el listado no expone la categoría real del producto
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
        "Grupo Diasa",
        lambda: descargar_y_normalizar_por_categoria(
            CATEGORIAS, descargar_categoria, normalizar_producto
        ),
    )
