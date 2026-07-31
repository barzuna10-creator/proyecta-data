from datetime import datetime

import requests

from crawlers.comun import guardar_productos


API_URL = "https://carbonestore.cr/products.json"
SITIO = "https://carbonestore.cr"

TAMANO_PAGINA = 250


def descargar_productos():
    pagina = 1
    productos_totales = []

    while True:
        response = requests.get(
            API_URL,
            params={
                "limit": TAMANO_PAGINA,
                "page": pagina,
            },
            timeout=30,
        )

        response.raise_for_status()

        productos = response.json().get("products") or []

        if not productos:
            break

        productos_totales.extend(productos)

        print(
            f"Página {pagina}: "
            f"{len(productos)} productos"
        )

        pagina += 1

    return productos_totales


def normalizar_producto(producto):
    variantes = producto.get("variants") or []
    variante = variantes[0] if variantes else {}

    imagenes = producto.get("images") or []
    url_imagen = imagenes[0]["src"] if imagenes else None

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
        "descripcion": None,
        "url_imagen": url_imagen,
        "url_producto": f"{SITIO}/products/{producto.get('handle')}",
        "compra_online": 1 if variante.get("available") else 0,
        "fecha_actualizacion": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
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
