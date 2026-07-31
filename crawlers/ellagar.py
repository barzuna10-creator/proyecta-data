import re
import unicodedata
from datetime import datetime

import requests

from crawlers.comun import guardar_productos


API_URL = (
    "https://www.ellagar.com/ECOMMERCE/API/Articulo/"
    "ObtenerArticulosPorCategoriaSubcategorias"
)

CATEGORIAS = {
    "Herramientas": 7,
    "Construccion": 9,
    "Pinturas": 10,
    "Electricidad": 11,
}


def convertir_a_slug(texto):
    if not texto:
        return ""

    texto = str(texto).lower()
    texto = unicodedata.normalize("NFD", texto)

    texto = "".join(
        caracter
        for caracter in texto
        if unicodedata.category(caracter) != "Mn"
    )

    texto = re.sub(r"[^a-z0-9]+", "-", texto)

    return texto.strip("-")


def construir_url_producto(producto_id, nombre):
    slug = convertir_a_slug(nombre)

    return (
        "https://www.ellagar.com/ECOMMERCE/"
        f"DetalleArticulo/{producto_id}/{slug}"
    )


def descargar_categoria(categoria_id):
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://www.ellagar.com",
        "Referer": "https://www.ellagar.com",
    }

    pagina = 1
    productos_totales = []

    while True:
        payload = {
            "Filtros": {
                "Categoria": categoria_id,
                "Subcategorias": "",
                "Precio_Maximo": 0,
                "Precio_Minimo": 0,
                "Ordenamiento": 0,
            },
            "Pagina": pagina,
            "TamanoPagina": 1000,
            "Agrupacion_Id": 0,
        }

        response = requests.post(
            API_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )

        response.raise_for_status()

        respuesta = response.json()
        data = respuesta.get("Data") or {}
        productos = data.get("PaginaItems") or []

        if not productos:
            break

        productos_totales.extend(productos)

        print(
            f"Página {pagina}: "
            f"{len(productos)} productos"
        )

        total_items = data.get("TotalItems", 0)

        if len(productos_totales) >= total_items:
            break

        pagina += 1

    return productos_totales


def normalizar_producto(producto, categoria_nombre):
    categoria = producto.get("Categoria") or {}
    subcategoria = producto.get("SubCategoria") or {}
    marca = producto.get("Marca") or {}

    producto_id = producto.get("ID")
    nombre = producto.get("Nombre")

    return {
        "proveedor": "El Lagar",
        "id_proveedor": str(producto_id),
        "sku": producto.get("Codigo_Principal"),
        "nombre": nombre,
        "marca": marca.get("Nombre"),
        "categoria": categoria.get(
            "Nombre",
            categoria_nombre,
        ),
        "subcategoria": subcategoria.get("Nombre"),
        "precio": producto.get("Precio"),
        "iva": 1 if producto.get("IVA") else 0,
        "cabys": producto.get("CABYS"),
        "descripcion": producto.get("Descripcion"),
        "url_imagen": producto.get("URLImagen"),
        "url_producto": construir_url_producto(
            producto_id,
            nombre,
        ),
        "compra_online": (
            1
            if producto.get("Permite_Compra_Ecommerce")
            else 0
        ),
        "fecha_actualizacion": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }


def actualizar():
    print("\n=== ACTUALIZANDO EL LAGAR ===\n")

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
        f"✅ El Lagar actualizado: "
        f"{cantidad} productos."
    )

    return cantidad