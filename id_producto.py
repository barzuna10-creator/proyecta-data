"""Decodifica el `id` que usa la URL /producto/{id} del frontend --
contraparte exacta de `idDeProducto()` en proyecta-web/app/lib/
productoCache.ts. No hay un id numérico de producto en este catálogo (la
llave real es proveedor+id_proveedor, ver productos.UNIQUE), así que el
frontend arma ese id codificando en base64url (sin relleno) la propia
`url_producto` del producto en el sitio del proveedor -- es estable,
único (verificado: cero duplicados en las 60,421 filas reales) y no
requiere agregar ninguna columna nueva.

Antes de esto, /producto/{id} solo se podía resolver desde
sessionStorage (poblado al navegar desde resultados de búsqueda) -- un
link compartido, recargado, o abierto en una pestaña nueva siempre caía
en "Producto no disponible", indistinguible de un producto realmente
eliminado (ver RELEASE_CANDIDATE.md). Esto no cambia el esquema del id
ni rompe ningún link ya generado -- solo agrega la forma de resolverlo
también desde el backend."""

import base64


def id_producto_a_url(id_codificado):
    """Inverso exacto de idDeProducto(): reconstruye la url_producto
    original a partir del id de la URL. None si el id no es un base64url
    válido (nunca lanza) -- un id corrupto/inventado a mano debe
    resultar en "producto no encontrado", no en un error 500."""

    if not id_codificado:
        return None

    relleno = "=" * (-len(id_codificado) % 4)
    try:
        return base64.urlsafe_b64decode(id_codificado + relleno).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
