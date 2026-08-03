from datetime import datetime

import requests

from crawlers.comun import (
    CABECERAS_NAVEGADOR,
    descargar_paginado,
    guardar_productos,
    limpiar_html,
    pedir_con_reintentos,
    serializar_imagenes,
)


API_URL = "https://carbonestore.cr/products.json"
SITIO = "https://carbonestore.cr"

TAMANO_PAGINA = 250


def descargar_productos():
    def pedir_pagina(pagina):
        response = pedir_con_reintentos(
            requests.get,
            API_URL,
            params={
                "limit": TAMANO_PAGINA,
                "page": pagina,
            },
            headers=CABECERAS_NAVEGADOR,
            timeout=30,
        )

        response.raise_for_status()

        productos = response.json().get("products") or []

        # Carbone no expone un total de páginas -- la única señal de que
        # se acabó es una página vacía, que descargar_paginado ya detecta
        # por su cuenta antes de mirar este segundo valor.
        return productos, False

    # el endpoint empieza a devolver 429 tras ~28 páginas seguidas
    return descargar_paginado(pedir_pagina, pausa_entre_paginas=0.6)


def normalizar_producto(producto):
    variantes = producto.get("variants") or []
    variante = variantes[0] if variantes else {}

    imagenes = producto.get("images") or []
    url_imagen = imagenes[0]["src"] if imagenes else None
    imagenes_extra = [img.get("src") for img in imagenes[1:]]

    precio = variante.get("price")

    return {
        "proveedor": "Carbone Store",
        "id_proveedor": str(producto.get("id")),
        "sku": variante.get("sku"),
        "nombre": producto.get("title"),
        "marca": producto.get("vendor"),
        "categoria": producto.get("product_type") or "General",
        "subcategoria": None,
        "precio": round(float(precio)) if precio is not None else None,
        "iva": None,
        "cabys": None,
        "descripcion": limpiar_html(producto.get("body_html")),
        "url_imagen": url_imagen,
        "url_producto": f"{SITIO}/products/{producto.get('handle')}",
        "compra_online": 1 if variante.get("available") else 0,
        "fecha_actualizacion": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        # "weight" del variant viene en 0 para prácticamente todo el
        # catálogo muestreado -- no se guarda para no llenar la columna de
        # ceros sin significado real.
        "imagenes_adicionales": serializar_imagenes(imagenes_extra),
    }


def actualizar():
    print("\n=== ACTUALIZANDO CARBONE STORE ===\n")

    try:
        productos = descargar_productos()

    except requests.RequestException as error:
        print(f"Error descargando Carbone Store: {error}")
        return 0

    productos_normalizados = [
        normalizar_producto(producto) for producto in productos
    ]

    cantidad = guardar_productos(productos_normalizados)

    print(
        f"✅ Carbone Store actualizado: "
        f"{cantidad} productos."
    )

    return cantidad
