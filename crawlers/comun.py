"""Código compartido entre los scrapers de proveedores.

Cada proveedor tiene su propia forma de descargar y normalizar productos,
pero todos terminan guardando el mismo esquema de columnas en la misma
base de datos. Esa parte común vive aquí para no reescribirla por
proveedor.
"""

import json
import time

import requests
from lxml import html as lxml_html

from db import conectar

# Algunos proveedores (confirmado con Carbone Store, detrás de Cloudflare)
# bloquean el User-Agent por defecto de `requests` como si fuera un bot,
# aunque el mismo request con curl o desde un navegador funciona sin
# problema. Cabecera compartida para los crawlers que la necesiten.
CABECERAS_NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


def limpiar_html(html_bruto):
    """Convierte una descripción en HTML (como la que devuelven las APIs de
    EPA y Carbone Store) a texto plano legible, preservando saltos de línea
    entre párrafos. Devuelve None si no hay nada que limpiar, para no guardar
    strings vacíos en la base de datos."""

    if not html_bruto or not html_bruto.strip():
        return None

    arbol = lxml_html.fromstring(html_bruto)

    # text_content() incluye el texto de <style>/<script> como si fuera
    # contenido visible -- hay que quitarlos antes de extraer texto (visto
    # en descripciones de Carbone Store con tablas de ficha técnica que
    # traen su propio <style> inline).
    for etiqueta in arbol.xpath("//style | //script"):
        etiqueta.drop_tree()

    for etiqueta in arbol.xpath("//br | //p | //li | //div | //tr"):
        etiqueta.tail = "\n" + (etiqueta.tail or "")

    lineas = [linea.strip() for linea in arbol.text_content().splitlines()]
    texto = "\n".join(linea for linea in lineas if linea)

    return texto or None


def pedir_con_reintentos(funcion_request, *args, reintentos=3, espera_base=3, **kwargs):
    """Envoltorio delgado sobre una llamada de `requests` (get/post) que
    reintenta ante fallos transitorios de red (timeout, conexión reiniciada,
    etc.) en vez de abortar todo el crawler -- visto en la práctica con
    Carbone Store, que cortó la conexión a mitad de una descarga completa
    y tiró todo el trabajo ya hecho porque `descargar_productos()` no
    guarda nada hasta terminar. No reintenta errores HTTP 4xx/5xx (esos no
    son transitorios; `raise_for_status()` los deja pasar tal cual)."""

    ultimo_error = None

    for intento in range(1, reintentos + 1):
        try:
            return funcion_request(*args, **kwargs)
        except requests.exceptions.RequestException as error:
            ultimo_error = error
            if intento < reintentos:
                time.sleep(espera_base * intento)

    raise ultimo_error


def serializar_imagenes(urls):
    """Guarda una lista de URLs de imagen adicionales como JSON. Filtra
    vacíos/duplicados y devuelve None (no "[]") cuando no queda ninguna,
    para que la ausencia de imágenes extra se distinga de una lista vacía
    guardada por error."""

    if not urls:
        return None

    vistas = []
    for url in urls:
        if url and url not in vistas:
            vistas.append(url)

    return json.dumps(vistas, ensure_ascii=False) if vistas else None


def guardar_productos(productos):
    conexion = conectar()
    cursor = conexion.cursor()

    guardados = 0

    for producto in productos:
        cursor.execute(
            """
            INSERT INTO productos (
                proveedor,
                id_proveedor,
                sku,
                nombre,
                marca,
                categoria,
                subcategoria,
                precio,
                iva,
                cabys,
                descripcion,
                url_imagen,
                url_producto,
                compra_online,
                fecha_actualizacion,
                peso,
                imagenes_adicionales
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(proveedor, id_proveedor)
            DO UPDATE SET
                -- sku/marca/subcategoria/descripcion/peso/imagenes_adicionales
                -- se completan en pasadas distintas según el proveedor (ej.
                -- El Lagar: el listado trae la fila base primero y el
                -- detalle por producto la enriquece después, en otra
                -- llamada). COALESCE evita que una pasada que no trae un
                -- campo (NULL) borre lo que una pasada anterior sí había
                -- guardado -- sin esto, re-guardar el listado base pisaba
                -- la marca/descripción ya enriquecidas de una corrida previa.
                sku = COALESCE(excluded.sku, productos.sku),
                nombre = excluded.nombre,
                marca = COALESCE(excluded.marca, productos.marca),
                categoria = excluded.categoria,
                subcategoria = COALESCE(excluded.subcategoria, productos.subcategoria),
                precio = excluded.precio,
                iva = excluded.iva,
                cabys = excluded.cabys,
                descripcion = COALESCE(excluded.descripcion, productos.descripcion),
                url_imagen = excluded.url_imagen,
                url_producto = excluded.url_producto,
                compra_online = excluded.compra_online,
                fecha_actualizacion = excluded.fecha_actualizacion,
                peso = COALESCE(excluded.peso, productos.peso),
                imagenes_adicionales = COALESCE(excluded.imagenes_adicionales, productos.imagenes_adicionales)
            """,
            (
                producto["proveedor"],
                producto["id_proveedor"],
                producto["sku"],
                producto["nombre"],
                producto["marca"],
                producto["categoria"],
                producto["subcategoria"],
                producto["precio"],
                producto["iva"],
                producto["cabys"],
                producto["descripcion"],
                producto["url_imagen"],
                producto["url_producto"],
                producto["compra_online"],
                producto["fecha_actualizacion"],
                producto.get("peso"),
                producto.get("imagenes_adicionales"),
            ),
        )

        guardados += 1

    conexion.commit()
    conexion.close()

    return guardados
