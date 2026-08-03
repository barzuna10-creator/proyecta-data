"""Construplaza no tiene una API propia documentada -- su listado de
productos se renderiza 100% en el navegador vía Algolia InstantSearch
(la página no trae ni un producto en el HTML servidor, ver
ESTRATEGIA_EXPANSION_PROVEEDORES.md). Investigando su propio bundle JS
(`/bundles/RequeriredUtils`) se encontró la inicialización real:

    algoliasearch("MUCJNSQCZH", "548b5dedba445fcdb9435d2dd720562a")

Esa Search API Key es la misma que usa el navegador de cualquier
visitante anónimo del sitio -- Algolia separa a propósito una clave de
solo lectura/búsqueda (segura para exponer en el frontend, sin permisos
de escritura) de la clave de administración (nunca vive en el cliente).
Se confirmó con una consulta real de solo lectura contra la API pública
de Algolia (`{app_id}-dsn.algolia.net`) antes de escribir este archivo.
No es scraping de HTML ni depende de ningún selector CSS -- es la misma
API JSON estructurada que ya usan EPA y El Lagar, solo que de un
proveedor externo (Algolia) en vez de un backend propio de Construplaza.
"""

import base64
from datetime import datetime

import requests

from crawlers.comun import (
    descargar_paginado,
    ejecutar_actualizacion,
    limpiar_html,
    pedir_con_reintentos,
)

APP_ID = "MUCJNSQCZH"
API_KEY = "548b5dedba445fcdb9435d2dd720562a"
API_URL = f"https://{APP_ID}-dsn.algolia.net/1/indexes/Products/query"
SITIO = "https://www.construplaza.com"

# Auditoría real del catálogo completo (21,527 productos, ver
# ARQUITECTURA_CRAWLERS.md / CONSTRUPLAZA_INTEGRACION.md): el 98.8% es
# material de ferretería/construcción genuino. Solo tres bolsas de ruido
# real, todas dentro del departamento "Organización" salvo "Servicios":
# cuidado automotriz (subcategoría "auto", 130 productos -- cera para
# carro, gatos hidráulicos, líquido de frenos), comida/bebida (subcategoría
# "alimento", 118 productos -- café, azúcar, gaseosas) y "Servicios" (3
# productos -- son servicios de taller, no productos físicos con SKU/stock
# comparable). Se descartan antes de normalizar, no después -- no tiene
# sentido construir un producto completo para tirarlo.
DEPARTAMENTO_SERVICIOS = "Servicios"
DEPARTAMENTO_ORGANIZACION = "Organización"
SUBCATEGORIAS_IRRELEVANTES_EN_ORGANIZACION = {"auto", "alimento"}


def _es_relevante(producto_crudo):
    departamento = producto_crudo.get("Departamento")

    if departamento == DEPARTAMENTO_SERVICIOS:
        return False

    if (
        departamento == DEPARTAMENTO_ORGANIZACION
        and producto_crudo.get("Subcategoria") in SUBCATEGORIAS_IRRELEVANTES_EN_ORGANIZACION
    ):
        return False

    return True

CABECERAS = {
    "X-Algolia-API-Key": API_KEY,
    "X-Algolia-Application-Id": APP_ID,
    "Content-Type": "application/json",
}

# 1000 es el máximo que permite Algolia por página -- con ~21,500
# productos, alcanza con ~22 páginas (contra las ~500-4000 llamadas que
# necesitan otros proveedores).
TAMANO_PAGINA = 1000


def construir_url_producto(articulo):
    """El sitio no tiene una URL de producto legible -- el resultado de
    búsqueda es un modal ("javascript:void(0)" en el propio HTML), no un
    link real. Pero cada tarjeta de producto trae un botón "compartir"
    con un link real y funcional: https://construplaza.com/P/{base64(
    código de artículo)}. Verificado a mano que resuelve (200, con
    redirect a la versión con www) y muestra el producto correcto antes
    de usar este patrón acá."""

    codificado = base64.b64encode(articulo.encode()).decode()
    return f"{SITIO}/P/{codificado}"


def descargar_productos():
    def pedir_pagina(pagina):
        # descargar_paginado() cuenta las páginas empezando en 1; Algolia
        # las cuenta empezando en 0 -- se convierte acá, en el único
        # lugar que necesita saberlo.
        pagina_algolia = pagina - 1

        payload = {"params": f"query=&hitsPerPage={TAMANO_PAGINA}&page={pagina_algolia}"}

        response = pedir_con_reintentos(
            requests.post,
            API_URL,
            headers=CABECERAS,
            json=payload,
            timeout=30,
        )

        response.raise_for_status()

        datos = response.json()
        productos = datos.get("hits") or []
        total_paginas = datos.get("nbPages", pagina_algolia + 1)

        return productos, (pagina_algolia + 1) >= total_paginas

    return descargar_paginado(pedir_pagina)


def normalizar_producto(producto):
    precio = producto.get("Precio")

    return {
        "proveedor": "Construplaza",
        "id_proveedor": producto.get("Articulo"),
        "sku": producto.get("Articulo"),
        "nombre": producto.get("Descripcion"),
        "marca": producto.get("Marca") or None,
        "categoria": producto.get("Departamento") or "General",
        "subcategoria": producto.get("Subcategoria"),
        # El precio de Algolia trae ruido de coma flotante (ej.
        # 7150.0000019 en vez de 7150) -- se redondea igual que hacen
        # EPA, Carbone Store y Ferretería Brenes con sus propios precios.
        "precio": round(precio) if precio is not None else None,
        # El índice no expone la tasa de IVA en un campo utilizable
        # ("Impuesto" es un código de categoría fiscal tipo "VTA", no un
        # porcentaje ni un booleano) -- se deja sin inventar un valor.
        "iva": None,
        "cabys": producto.get("Cabys"),
        "descripcion": limpiar_html(producto.get("Notas")),
        "url_imagen": producto.get("Image"),
        "url_producto": construir_url_producto(producto.get("Articulo")),
        # Sin señal de disponibilidad/stock en el índice de Algolia (no
        # hay ningún campo booleano ni de cantidad) -- se deja sin
        # inventar un valor, a diferencia de los otros 4 proveedores que
        # sí tienen un campo real para esto. Ver limitaciones documentadas.
        "compra_online": None,
        "fecha_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "peso": str(producto.get("Peso")) if producto.get("Peso") is not None else None,
    }


def actualizar():
    return ejecutar_actualizacion(
        "Construplaza",
        lambda: [
            normalizar_producto(p) for p in descargar_productos() if _es_relevante(p)
        ],
    )
