"""Novex Costa Rica (novex.cr) no tiene una API pública documentada -- es
una plataforma propia (no Shopify/WooCommerce/Magento/PrestaShop/Algolia,
descartados explícitamente antes de escribir esto, ver NOVEX_FACTIBILIDAD.md
y NOVEX_SPIKE.md). Se descubrieron dos piezas reales durante la
investigación:

1. El árbol de categorías completo (departamento + ~1000 categorías hoja)
   ya viene en el HTML crudo de la página de inicio -- no hace falta
   ejecutar JavaScript ni un navegador real para leerlo, solo un GET plano.

2. Cada categoría hoja tiene un endpoint de paginación real: un POST a su
   propia URL (con `?page=N` en el query string -- el número de página
   vive ahí, no solo en el cuerpo del POST, hallazgo confirmado con el
   spike) que devuelve JSON estructurado real (`articles.data`), no HTML
   para parsear. El slug de la URL es cosmético -- solo importa el id
   numérico de categoría/producto (verificado a mano con un slug
   inventado que igual resolvió al producto correcto).

Novex sí tiene Cloudflare con bot-management activo (cookie `__cf_bm`) --
a diferencia de los demás proveedores, acá hace falta sesión persistente
(cookies) y espaciado real entre peticiones. El spike técnico (ver
NOVEX_SPIKE.md) confirmó 0 bloqueos en 100 peticiones sostenidas reales
con 2 segundos de pausa -- ese es el valor que se usa acá, no uno menor.

Novex tiene departamentos completos ajenos a construcción (Mascotas,
Decoración, Electrodomésticos, etc. -- confirmado por auditoría real de
los 27 departamentos raíz, ver NOVEX_FACTIBILIDAD.md) que se descartan
antes de descargar un solo producto de ahí, igual que se hizo con
Construplaza."""

from datetime import datetime
import re
import time

import requests

from crawlers.comun import (
    descargar_paginado,
    descargar_y_normalizar_por_categoria,
    ejecutar_actualizacion,
    limpiar_html,
    pedir_con_reintentos,
)

SITIO = "https://novex.cr"

CABECERAS_NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# Confirmado con un spike real (ver NOVEX_SPIKE.md): 0 bloqueos de
# Cloudflare en 100 peticiones sostenidas con esta pausa. No bajar este
# valor sin repetir la prueba de estabilidad -- Novex, a diferencia de los
# demás proveedores, sí tiene bot-management activo.
PAUSA_ENTRE_PETICIONES = 2.0

# Departamentos raíz confirmados como ajenos a construcción/remodelación
# (auditoría real de los 27 departamentos, ver NOVEX_FACTIBILIDAD.md) --
# se excluyen por nombre, igual que Construplaza excluye por categoría.
DEPARTAMENTOS_EXCLUIDOS = {
    "Electrodomésticos",
    "Cocina",
    "Comedor y bar",
    "Decoración",
    "Outdoors",
    "Mascotas",
    "Muebles",
    "Automotriz",
}

_PATRON_DEPARTAMENTOS = re.compile(
    r'<a[^>]*href="https://www\.novex\.cr/catalogo/(\d{2})/[^"]+\.html"[^>]*>([^<]*)<'
)
_PATRON_HOJAS = re.compile(
    r'<a title="([^"]*)" href="https://www\.novex\.cr/catalogo/(\d{6})/([^"]+)\.html"'
)


def _extraer_categorias(html_home):
    """Parsea el HTML crudo de la home (ya trae el árbol completo, ver
    docstring del módulo) y devuelve {nombre_hoja: (id_hoja, slug,
    departamento)} para las hojas que NO pertenecen a un departamento
    excluido. La clave del diccionario es solo el nombre -- si dos hojas
    de departamentos distintos comparten nombre, es una colisión rara y
    aceptable (mismo trade-off que ya asumen EPA/Brenes con sus propios
    diccionarios CATEGORIAS)."""

    # El menú aparece más de una vez en el HTML (versión de escritorio y un
    # duplicado -- probablemente el menú móvil -- con el texto vacío). Se
    # queda con la primera aparición real de cada id y se ignoran las
    # repeticiones en blanco, en vez de construir el dict directamente
    # (eso dejaba que la aparición vacía pisara el nombre real, según
    # cuál viniera última en el HTML).
    departamentos = {}
    for id_depto, nombre_depto in _PATRON_DEPARTAMENTOS.findall(html_home):
        nombre_depto = nombre_depto.strip()
        if nombre_depto and id_depto not in departamentos:
            departamentos[id_depto] = nombre_depto

    categorias = {}
    for nombre_hoja, id_hoja, slug in _PATRON_HOJAS.findall(html_home):
        nombre_departamento = departamentos.get(id_hoja[:2], "").strip()
        if nombre_departamento in DEPARTAMENTOS_EXCLUIDOS:
            continue
        categorias[nombre_hoja.strip()] = (id_hoja, slug, nombre_departamento or "General")

    return categorias


def _pedir_categoria_paginada(sesion, id_categoria, slug):
    """Descarga todas las páginas de una categoría hoja vía el endpoint
    JSON real (POST con el número de página en el query string, ver
    docstring del módulo). Una categoría con respuesta no-JSON o vacía
    simplemente no aporta productos -- no aborta la corrida completa
    (mismo criterio que descargar_y_normalizar_por_categoria ya aplica a
    nivel de categoría para EPA/Brenes)."""

    url_categoria = f"{SITIO}/catalogo/{id_categoria}/{slug}.html"

    def pedir_pagina(pagina):
        url_pagina = f"{url_categoria}?page={pagina}"
        respuesta = pedir_con_reintentos(
            sesion.post,
            url_pagina,
            data={"cmd": "page", "view": "grid", "page": pagina, "c": id_categoria},
            headers={"Referer": url_pagina, "Origin": SITIO},
            timeout=20,
        )

        try:
            datos = respuesta.json()
        except ValueError:
            return [], True

        articulos = datos.get("articles") or {}
        productos = articulos.get("data") or []
        pagina_actual = articulos.get("current_page", pagina)
        ultima_pagina = articulos.get("last_page", pagina_actual)

        return productos, pagina_actual >= ultima_pagina

    return descargar_paginado(pedir_pagina, pausa_entre_paginas=PAUSA_ENTRE_PETICIONES)


def obtener_productos_normalizados():
    sesion = requests.Session()
    sesion.headers.update(CABECERAS_NAVEGADOR)

    respuesta_home = pedir_con_reintentos(sesion.get, f"{SITIO}/", timeout=20)
    categorias = _extraer_categorias(respuesta_home.text)
    print(f"{len(categorias)} categorías reales tras excluir departamentos ajenos a construcción.\n")

    def descargar_categoria(datos_categoria):
        id_categoria, slug, departamento = datos_categoria
        productos = _pedir_categoria_paginada(sesion, id_categoria, slug)
        for producto in productos:
            producto["_departamento"] = departamento
        time.sleep(PAUSA_ENTRE_PETICIONES)
        return productos

    return descargar_y_normalizar_por_categoria(categorias, descargar_categoria, normalizar_producto)


def construir_url_producto(sku):
    # El slug es cosmético -- el sitio resuelve por id sin importar el
    # texto que lo acompañe (verificado a mano con un slug inventado).
    return f"{SITIO}/producto/{sku}/producto.html"


def construir_url_imagen(sku):
    # Mismo host que aloja las imágenes de Construplaza (infraestructura
    # compartida del grupo Novex/Vidrí) -- versión "large", confirmada
    # contra el JSON-LD real de una página de producto.
    return f"https://ferreteriavidri.com/images/items/large/{sku}.jpg"


def _es_disponible(producto):
    en_linea = producto.get("online")
    if en_linea is None:
        return None
    return bool(en_linea)


# Novex nombra su propio departamento de pinturas en singular ("Pintura"),
# a diferencia de los otros 4 proveedores (todos "Pinturas", confirmado
# contra la base real) -- sin normalizar esto, familias.py nunca agruparía
# las variantes de presentación de Novex (CATEGORIAS_AGRUPABLES exige el
# string exacto "Pinturas"), el mismo tipo de inconsistencia visible que
# ya se encontró y corrigió con Construplaza (ver NOVEX_INTEGRACION.md).
_NORMALIZAR_DEPARTAMENTO = {"Pintura": "Pinturas"}


def normalizar_producto(producto, nombre_categoria):
    precio = producto.get("price")
    departamento = producto.get("_departamento") or "General"
    departamento = _NORMALIZAR_DEPARTAMENTO.get(departamento, departamento)

    return {
        "proveedor": "Novex",
        "id_proveedor": producto.get("sku"),
        "sku": producto.get("sku"),
        "nombre": producto.get("title"),
        "marca": producto.get("brand") or None,
        "categoria": departamento,
        "subcategoria": nombre_categoria,
        "precio": round(precio) if precio is not None else None,
        # Igual que Construplaza: no hay campo de tasa de IVA utilizable
        # en la fuente -- se deja sin inventar un valor.
        "iva": None,
        "cabys": None,
        "descripcion": limpiar_html(producto.get("shortDescription") or producto.get("specs")),
        "url_imagen": construir_url_imagen(producto.get("sku")),
        "url_producto": construir_url_producto(producto.get("sku")),
        "compra_online": _es_disponible(producto),
        "fecha_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # No se encontró un campo de peso utilizable en la fuente (ni en el
        # JSON de listado ni en el JSON-LD de detalle) -- ver limitaciones
        # documentadas en NOVEX_FACTIBILIDAD.md.
        "peso": None,
    }


def actualizar():
    return ejecutar_actualizacion("Novex", obtener_productos_normalizados)
