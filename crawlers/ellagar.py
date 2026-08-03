import re
import time
import unicodedata
from datetime import datetime
from urllib.parse import unquote

import requests

from crawlers import comun
from crawlers.comun import (
    descargar_paginado,
    descargar_y_normalizar_por_categoria,
    guardar_productos,
    limpiar_html,
    pedir_con_reintentos,
    serializar_imagenes,
)


API_URL = (
    "https://www.ellagar.com/ECOMMERCE/API/Articulo/"
    "ObtenerArticulosPorCategoriaSubcategorias"
)

API_URL_DETALLE = (
    "https://www.ellagar.com/ECOMMERCE/API/Articulo/ObtenerDetalleArticulo"
)

CABECERAS = {
    "Content-Type": "application/json",
    "Origin": "https://www.ellagar.com",
    "Referer": "https://www.ellagar.com",
}

# El Lagar solo expone marca/descripción/galería completas en el endpoint
# de detalle de UN producto a la vez (no en el listado por categoría) -- no
# hay forma de pedirlas en lote como con EPA o Carbone. Se agrega esta
# pausa entre llamadas para no golpear su servidor con ~4 mil requests
# seguidos; medido en la práctica, ~530ms por request ya de por sí, así
# que esto es un margen de cortesía adicional, no el cuello de botella.
PAUSA_ENTRE_DETALLES = 0.1

CATEGORIAS = {
    "Herramientas": 7,
    "Construccion": 9,
    "Pinturas": 10,
    "Electricidad": 11,
}

# El paso de detalle es el costoso (~1 request por producto, ~50 min para
# todo el catálogo) -- si el proceso se corta a mitad de camino (pasó en la
# práctica: la laptop se suspendió y quedó 7 horas colgado sin guardar
# nada), este checkpoint permite retomar sin repetir lo ya hecho en vez de
# volver a arrancar desde cero.
CHECKPOINT_PATH = "logs/ellagar_checkpoint.json"

# Un checkpoint más viejo que esto no se considera "la corrida de anoche
# que se cortó" sino uno olvidado -- se descarta para no bloquear un
# refresco completo indefinidamente (los precios cambian todos los días).
CHECKPOINT_MAX_HORAS = 6


# Persistencia del checkpoint: delegada a crawlers/comun.py (compartida
# con cualquier proveedor futuro que también enriquezca producto por
# producto), pero se mantienen estas funciones envoltorio con el mismo
# nombre de siempre -- tests/test_enriquecimiento.py las usa directamente
# vía mock.patch.object(ellagar, ...) y no había motivo para tocar ese
# contrato solo porque la implementación de adentro ahora es compartida.
def _cargar_checkpoint():
    return comun.cargar_checkpoint(CHECKPOINT_PATH, CHECKPOINT_MAX_HORAS)


def _guardar_checkpoint(ids_completados):
    comun.guardar_checkpoint(CHECKPOINT_PATH, ids_completados)


def _borrar_checkpoint():
    comun.borrar_checkpoint(CHECKPOINT_PATH)


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

    # El Lagar no avisa "esta es la última página" por sí sola -- hay que
    # comparar cuántos productos se llevan acumulados contra TotalItems,
    # que sí viene en cada respuesta. Se lleva la cuenta en esta variable
    # cerrada sobre pedir_pagina en vez de en descargar_paginado (que no
    # sabe nada de "total_items", solo de "productos"/"es_ultima").
    acumulado = 0

    def pedir_pagina(pagina):
        nonlocal acumulado

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

        response = pedir_con_reintentos(
            requests.post,
            API_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )

        response.raise_for_status()

        respuesta = response.json()
        data = respuesta.get("Data") or {}
        productos = data.get("PaginaItems") or []

        acumulado += len(productos)
        total_items = data.get("TotalItems", 0)

        return productos, acumulado >= total_items

    return descargar_paginado(pedir_pagina)


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


def obtener_detalle(articulo_id):
    """El Lagar solo expone marca, descripción completa y galería de
    imágenes en el endpoint de detalle de un artículo individual -- el
    listado por categoría (`descargar_categoria`) trae esos mismos campos
    en el JSON pero vacíos en la práctica (confirmado contra el catálogo
    real, no es un supuesto). Devuelve un diccionario con solo lo que este
    endpoint aporta de más; None si la llamada falla, para que el llamador
    decida no perder el producto solo porque no se pudo enriquecer."""

    body = {
        "SocioId": 0,
        "ArticuloId": str(articulo_id),
        "AplicarHistorial": True,
        "SucursalRecoge": 0,
        "Agrupacion_Id": 0,
    }

    try:
        respuesta = pedir_con_reintentos(
            requests.post,
            API_URL_DETALLE,
            json=body,
            headers=CABECERAS,
            timeout=15,
            reintentos=2,
            espera_base=2,
        )
        respuesta.raise_for_status()
        datos = (respuesta.json() or {}).get("Data") or {}
    except (requests.RequestException, ValueError):
        return None

    marca = (datos.get("Marca") or {}).get("Nombre")

    # La descripción viene con entidades HTML URL-encodadas
    # (ej. "%3Cul%3E...") -- se decodifica antes de reutilizar el mismo
    # limpiador de HTML que ya usan EPA y Carbone Store.
    descripcion_cruda = datos.get("Descripcion")
    descripcion = limpiar_html(unquote(descripcion_cruda)) if descripcion_cruda else None

    galeria = datos.get("URLGaleria") or []
    imagen_principal = datos.get("URLImagen")
    imagenes_extra = [url for url in galeria if url != imagen_principal]

    return {
        "marca": marca,
        "descripcion": descripcion,
        "imagenes_adicionales": serializar_imagenes(imagenes_extra),
    }


def actualizar(con_detalle=True):
    print("\n=== ACTUALIZANDO EL LAGAR ===\n")

    productos_normalizados = descargar_y_normalizar_por_categoria(
        CATEGORIAS, descargar_categoria, normalizar_producto
    )

    # Se guarda el listado base de una vez, antes de arrancar el
    # enriquecimiento por producto -- así, si el proceso de detalle se
    # interrumpe (red, corte, laptop suspendida a media noche -- pasó en la
    # práctica corriendo esto), el catálogo ya quedó al día con los datos
    # del listado en vez de perder toda la corrida.
    cantidad = guardar_productos(productos_normalizados)
    print(f"Listado base guardado: {cantidad} productos.\n")

    if con_detalle:
        total = len(productos_normalizados)
        ids_completados = _cargar_checkpoint()
        omitidos_de_entrada = len(
            ids_completados & {p["id_proveedor"] for p in productos_normalizados}
        )
        print(
            f"Enriqueciendo {total} productos con detalle individual "
            f"({omitidos_de_entrada} ya completados en un checkpoint previo)...\n"
        )
        errores_detalle = 0
        omitidos = 0
        TAMANO_LOTE = 200
        lote_actual = []

        for indice, producto in enumerate(productos_normalizados, start=1):
            id_producto = producto["id_proveedor"]

            if id_producto in ids_completados:
                omitidos += 1
            else:
                detalle = obtener_detalle(id_producto)

                if detalle is None:
                    errores_detalle += 1
                else:
                    if detalle["marca"]:
                        producto["marca"] = detalle["marca"]
                    if detalle["descripcion"]:
                        producto["descripcion"] = detalle["descripcion"]
                    producto["imagenes_adicionales"] = detalle["imagenes_adicionales"]

                lote_actual.append(producto)
                ids_completados.add(id_producto)
                time.sleep(PAUSA_ENTRE_DETALLES)

            # Guardar cada TAMANO_LOTE productos (no solo al final) para no
            # perder el progreso si el proceso se corta a mitad de camino.
            # COALESCE en guardar_productos evita que este guardado parcial
            # borre marca/descripción de productos que este lote no tocó.
            if len(lote_actual) >= TAMANO_LOTE or indice == total:
                if lote_actual:
                    guardar_productos(lote_actual)
                    lote_actual = []
                _guardar_checkpoint(ids_completados)
                print(f"  {indice}/{total} procesados ({omitidos} por checkpoint)...")

        _borrar_checkpoint()
        print(
            f"Detalle completado -- {total - errores_detalle - omitidos}/{total} "
            f"enriquecidos esta corrida, {omitidos} venían de un checkpoint previo, "
            f"{errores_detalle} con error (quedan con los datos del listado, "
            "no se descartan).\n"
        )

    print(
        f"✅ El Lagar actualizado: "
        f"{cantidad} productos."
    )

    return cantidad