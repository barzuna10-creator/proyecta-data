"""Código compartido entre los scrapers de proveedores.

Cada proveedor tiene su propia forma de descargar y normalizar productos,
pero todos terminan guardando el mismo esquema de columnas en la misma
base de datos. Esa parte común vive aquí para no reescribirla por
proveedor.
"""

import json
import os
import time
from datetime import datetime

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


# --- Paginación y orquestación compartidas -----------------------------
#
# Los 4 proveedores actuales (y cualquiera nuevo con una API de listado
# paginada) comparten la misma forma: pedir una página, acumular,
# imprimir progreso, y parar cuando la página viene vacía o el propio
# proveedor avisa que era la última. Lo único que cambia entre
# proveedores es CÓMO se pide una página y CÓMO se sabe que es la última
# -- eso vive en la función `pedir_pagina` que cada proveedor arma con su
# propia lógica de request/respuesta, nunca acá.


def descargar_paginado(pedir_pagina, pausa_entre_paginas=0):
    """Bucle de paginación genérico.

    `pedir_pagina(pagina: int) -> (productos: list, es_ultima_pagina: bool)`
    es la única pieza que varía por proveedor. El bucle para cuando la
    página viene vacía (sin importar lo que diga `es_ultima_pagina` --
    una página vacía es señal suficiente por sí sola) o cuando
    `pedir_pagina` devuelve `es_ultima_pagina=True`.

    `pausa_entre_paginas`: segundos de espera antes de pedir la
    siguiente página, solo si esta página no era la última (algunos
    proveedores empiezan a devolver 429 tras muchas páginas seguidas;
    ver crawlers/carbone.py). 0 (por defecto) no espera nada -- la
    mayoría de los proveedores no lo necesitan."""

    pagina = 1
    productos_totales = []

    while True:
        productos, es_ultima_pagina = pedir_pagina(pagina)

        if not productos:
            break

        productos_totales.extend(productos)
        print(f"Página {pagina}: {len(productos)} productos")

        if es_ultima_pagina:
            break

        pagina += 1
        if pausa_entre_paginas:
            time.sleep(pausa_entre_paginas)

    return productos_totales


def descargar_y_normalizar_por_categoria(categorias, descargar_categoria, normalizar_producto):
    """Recorre un diccionario {nombre_categoria: id_categoria}, descarga y
    normaliza cada una con las funciones que el proveedor le pasa. Si una
    categoría falla (error de red que agotó los reintentos), se salta y
    se sigue con las demás -- nunca aborta la corrida completa por una
    categoría caída. Devuelve la lista plana de productos ya normalizados,
    lista para guardar_productos()."""

    productos_normalizados = []

    for nombre_categoria, categoria_id in categorias.items():
        print(f"Descargando {nombre_categoria}...")

        try:
            productos = descargar_categoria(categoria_id)
        except requests.RequestException as error:
            print(f"Error descargando {nombre_categoria}: {error}")
            continue

        for producto in productos:
            productos_normalizados.append(normalizar_producto(producto, nombre_categoria))

        print(f"{len(productos)} productos descargados.\n")

    return productos_normalizados


def ejecutar_actualizacion(nombre_proveedor, obtener_productos_normalizados, forma_verbo="actualizado"):
    """Envoltorio común para `actualizar()`: imprime el banner de inicio,
    corre `obtener_productos_normalizados()` (que ya trae la lista de
    productos normalizados, sin importar cómo los consiguió), guarda en
    la base de datos, imprime el resumen final, y devuelve la cantidad
    guardada -- exactamente lo que `main.py` espera de cada proveedor.

    `forma_verbo` existe solo por concordancia de género en español
    ("EPA actualizado" vs. "Ferretería Brenes actualizada") -- no cambia
    ningún comportamiento, solo el texto del mensaje final."""

    print(f"\n=== ACTUALIZANDO {nombre_proveedor.upper()} ===\n")

    productos_normalizados = obtener_productos_normalizados()
    cantidad = guardar_productos(productos_normalizados)

    print(f"✅ {nombre_proveedor} {forma_verbo}: {cantidad} productos.")

    return cantidad


# --- Checkpoint incremental compartido ----------------------------------
#
# Hoy solo El Lagar lo necesita (es el único proveedor que enriquece
# producto por producto con una llamada HTTP individual, ver
# crawlers/ellagar.py) pero cualquier proveedor futuro con el mismo
# patrón -- listado rápido en lote + detalle lento uno por uno -- puede
# reusar esto sin reimplementar el manejo de archivo ni el chequeo de
# antigüedad.


def cargar_checkpoint(ruta, max_horas):
    """Devuelve el set de ids ya completados en `ruta`, o un set vacío si
    no hay checkpoint, está corrupto, o es más viejo que `max_horas` (un
    checkpoint viejo no es "la corrida de anoche que se cortó", es uno
    olvidado -- se descarta para no bloquear un refresco completo)."""

    if not os.path.exists(ruta):
        return set()

    try:
        with open(ruta, encoding="utf-8") as archivo:
            datos = json.load(archivo)

        guardado_en = datetime.fromisoformat(datos["guardado_en"])
        edad_horas = (datetime.now() - guardado_en).total_seconds() / 3600

        if edad_horas > max_horas:
            print(f"Checkpoint tiene {edad_horas:.1f}h -- se descarta y se arranca de cero.\n")
            return set()

        ids = set(datos.get("ids_completados") or [])
        print(f"Checkpoint encontrado: {len(ids)} productos ya enriquecidos, se retoma.\n")
        return ids

    except (OSError, ValueError, KeyError):
        return set()


def guardar_checkpoint(ruta, ids_completados):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)

    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(
            {
                "guardado_en": datetime.now().isoformat(),
                "ids_completados": sorted(ids_completados),
            },
            archivo,
        )


def borrar_checkpoint(ruta):
    if os.path.exists(ruta):
        os.remove(ruta)
