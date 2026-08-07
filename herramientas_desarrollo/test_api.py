import time
from datetime import datetime

import pandas as pd
import requests


API_URL = (
    "https://www.ellagar.com/ECOMMERCE/API/Articulo/"
    "ObtenerArticulosPorCategoriaSubcategorias"
)

HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://www.ellagar.com",
    "Referer": "https://www.ellagar.com/",
}

# Por ahora tenemos confirmada la categoría 9.
# Luego agregaremos los IDs de las otras categorías.
CATEGORIAS = {
    "Construccion": 9,
}


def descargar_categoria(nombre_categoria, categoria_id):
    productos_totales = []
    pagina = 1
    tamano_pagina = 100

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
            "TamanoPagina": tamano_pagina,
            "Agrupacion_Id": 0,
        }

        print(
            f"Descargando {nombre_categoria} - página {pagina}..."
        )

        response = requests.post(
            API_URL,
            json=payload,
            headers=HEADERS,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        productos_pagina = (
            data.get("Data", {}).get("PaginaItems", [])
        )

        if not productos_pagina:
            break

        for producto in productos_pagina:
            categoria = producto.get("Categoria") or {}
            subcategoria = producto.get("SubCategoria") or {}
            marca = producto.get("Marca") or {}
            departamento = producto.get("Departamento") or {}

            productos_totales.append({
                "Proveedor": "El Lagar",
                "ID": producto.get("ID"),
                "Nombre": producto.get("Nombre"),
                "Codigo principal": producto.get(
                    "Codigo_Principal"
                ),
                "Codigo padre": producto.get(
                    "Codigo_Padre"
                ),
                "Codigo hijo": producto.get(
                    "Codigo_Hijo"
                ),
                "Precio": producto.get("Precio"),
                "Categoria": categoria.get(
                    "Nombre",
                    nombre_categoria,
                ),
                "Subcategoria": subcategoria.get("Nombre"),
                "Marca": marca.get("Nombre"),
                "Departamento": departamento.get("Nombre"),
                "Imagen pequena": producto.get("URLImagen"),
                "Imagen mediana": producto.get(
                    "URLImagenMedium"
                ),
                "Galeria": producto.get("URLGaleria"),
                "Fecha consulta": datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            })

        total_items = data.get("Data", {}).get("TotalItems", 0)

        if len(productos_totales) >= total_items:
            break

        pagina += 1

        # Pausa para no sobrecargar el sitio.
        time.sleep(1)

    print(
        f"{nombre_categoria}: "
        f"{len(productos_totales)} productos descargados."
    )

    return productos_totales


def main():
    todos_los_productos = []
    productos_por_categoria = {}

    for nombre, categoria_id in CATEGORIAS.items():
        try:
            productos = descargar_categoria(
                nombre,
                categoria_id,
            )

            productos_por_categoria[nombre] = productos
            todos_los_productos.extend(productos)

        except requests.RequestException as error:
            print(
                f"Error descargando {nombre}: {error}"
            )

    if not todos_los_productos:
        print("No se descargó ningún producto.")
        return

    nombre_archivo = "ellagar_catalogo_completo.xlsx"

    with pd.ExcelWriter(
        nombre_archivo,
        engine="openpyxl",
    ) as writer:

        # Todos los productos juntos.
        df_todos = pd.DataFrame(todos_los_productos)
        df_todos.to_excel(
            writer,
            sheet_name="Todos",
            index=False,
        )

        # Una pestaña por categoría.
        for nombre, productos in productos_por_categoria.items():
            if not productos:
                continue

            df_categoria = pd.DataFrame(productos)

            # Excel permite máximo 31 caracteres por pestaña.
            nombre_pestana = nombre[:31]

            df_categoria.to_excel(
                writer,
                sheet_name=nombre_pestana,
                index=False,
            )

    print()
    print(
        f"Excel creado: {nombre_archivo}"
    )
    print(
        f"Total de productos: {len(todos_los_productos)}"
    )


if __name__ == "__main__":
    main()