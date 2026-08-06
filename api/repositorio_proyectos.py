import json
import multiprocessing
import secrets
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from db import conectar
from busqueda import normalizar_texto
from especificaciones import unidad_comercial as _unidad_comercial
from api.adaptador_planos import construir_analisis_plano
from seleccion_automatica import (
    seleccionar_para_acabado as _seleccionar_para_acabado,
    seleccionar_para_pieza_estructural as _seleccionar_para_pieza_estructural,
    seleccionar_para_puerta_ventana as _seleccionar_para_puerta_ventana,
)
import eventos as _eventos

# INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md: leer un plano real (fitz +
# pdfplumber) es 100% CPU-bound en un solo núcleo durante ~10s para un PDF
# de 105MB -- medido en producción, esto congela TODA la app (crear
# proyectos, buscar, navegar) para cualquier otro usuario, porque aunque
# el endpoint ya corre en un hilo aparte (Starlette lo despacha solo), ese
# hilo sigue compitiendo por el mismo GIL que el resto del proceso. Un
# hilo no alcanza -- hace falta un PROCESO aparte, con su propio GIL.
#
# `spawn` explícito (no el default de la plataforma, que en Linux puede
# ser `fork`): este proceso ya tiene varios hilos corriendo (el threadpool
# de Starlette) para cuando se sube el primer plano -- hacer fork() de un
# proceso con hilos activos es una fuente clásica de deadlocks (un lock
# que otro hilo tenía tomado en el momento del fork queda tomado para
# siempre en el hijo, que nunca tendrá ese hilo para soltarlo). `spawn`
# arranca un intérprete de Python limpio en el hijo -- más lento de
# arrancar, pero seguro en cualquier plataforma (localhost/macOS y
# Render/Linux por igual).
#
# max_workers=1 a propósito: cada análisis mide ~380MB de RSS pico (ver
# INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md) -- permitir varios en
# paralelo multiplicaría ese pico en un entorno de memoria limitada. Con
# 1 worker, un segundo plano subido mientras el primero se procesa
# simplemente espera su turno (ProcessPoolExecutor ya encola eso solo) --
# lo que nunca espera es el resto de la aplicación, que es el requisito
# real.
_CONTEXTO_PROCESOS = multiprocessing.get_context("spawn")
_EXECUTOR_PLANOS = ProcessPoolExecutor(max_workers=1, mp_context=_CONTEXTO_PROCESOS)


def _procesar_plano_pdf(ruta_pdf):
    """Todo el trabajo CPU-intensivo de leer un plano -- exactamente la
    misma secuencia de llamadas que antes corría inline dentro de
    analizar_plano(), sin ningún cambio de comportamiento. Vive como
    función de nivel de módulo (no un closure, no un método) porque
    ProcessPoolExecutor necesita poder importar una referencia a ella por
    nombre calificado en el proceso hijo -- una función anidada no se
    puede *picklear* así.

    Devuelve únicamente el dict plano de construir_analisis_plano() --
    nunca un objeto de lectura_planos (Proyecto, Lamina, etc.) cruza la
    frontera entre procesos, evita cualquier duda sobre si algo ahí es
    picklable.

    Import diferido a propósito (investigación de memoria en el arranque
    en producción): lectura_planos carga fitz/pdfplumber/pdfminer, ~76MB
    -- si estuviera a nivel de módulo, se pagaría en CADA worker de
    uvicorn al arrancar, aunque nunca procese un plano. Acá solo se paga
    en el proceso hijo de ProcessPoolExecutor, y solo cuando de verdad
    hay un plano que leer -- nunca en el proceso principal de la API."""
    import lectura_planos as lp

    proyecto_leido = lp.leer_proyecto(ruta_pdf)
    modelo_edificio = lp.construir_modelo_edificio(proyecto_leido)
    cuadros = lp.agregar_cuadros(proyecto_leido)
    computo_estructural = lp.agregar_computo_estructural(proyecto_leido)
    return construir_analisis_plano(proyecto_leido, modelo_edificio, cuadros, computo_estructural)

ESTADOS_PROYECTO = {"activo", "completado", "archivado"}
ESTADOS_ITEM = {"pendiente", "comprado", "descartado"}

# De dónde puede venir un ítem agregado a un proyecto (ver
# AUDITORIA_INTEGRAL_PRODUCTO.md, hallazgo §1: "nunca se guarda de dónde
# salió un ítem"). "plantilla" es un origen real, no inventado para este
# cambio -- ya existía el flujo de sugerencias de plantilla de proyecto
# (SugerenciasMateriales.tsx / PLANTILLAS_PROYECTO_V1.md), solo no se
# distinguía de un ítem agregado a mano.
ORIGENES_ITEM_VALIDOS = {"plano", "sistema_constructivo", "plantilla", "manual"}

# Cada partida tiene un identificador estable (`id`, nunca cambia, es lo
# que debería usarse para reconocerla en código) y una representación
# visible independiente (`nombre`, lo que se guarda en items_proyecto.partida
# y lo que ve el usuario -- sigue siendo texto libre en la base de datos,
# el usuario lo puede editar como siempre). Único lugar donde vive el
# orden de construcción típico -- de cimentación hacia acabados, no
# alfabético, porque así es como se lee una cotización real. "Demolición",
# "Obra gris" y "Sanitarios" están acá por PLANTILLAS_PROYECTO_V1.md
# (remodelaciones), en su posición relativa original.
# (Ver también app/lib/partidas.ts en el frontend, misma lista y mismo
# orden -- ese archivo sigue siendo la fuente de la lista para la UI de
# selección manual, no se tocó en este cambio).
@dataclass(frozen=True)
class Partida:
    id: str
    nombre: str


PARTIDAS_SUGERIDAS = [
    Partida("demolicion", "Demolición"),
    Partida("obra_gris", "Obra gris"),
    Partida("cimentacion", "Cimentación"),
    Partida("estructura", "Estructura"),
    Partida("paredes", "Paredes"),
    Partida("techo", "Techo"),
    Partida("electrico", "Eléctrico"),
    Partida("hidraulico", "Hidráulico"),
    Partida("acabados", "Acabados"),
    Partida("pintura", "Pintura"),
    Partida("sanitarios", "Sanitarios"),
    Partida("otros", "Otros"),
]
_NOMBRES_PARTIDAS_SUGERIDAS = [partida.nombre for partida in PARTIDAS_SUGERIDAS]
_PARTIDA_POR_ID = {partida.id: partida for partida in PARTIDAS_SUGERIDAS}

# Auditoría de UX (ver UX_COTIZACION_AUDITORIA.md): organizar en partidas un
# proyecto de 30-80 ítems a mano, uno por uno, es el punto de fricción más
# grande del flujo real de cotizar. La categoría del producto (ya capturada
# en cada ítem) es una señal gratis para adelantar ese trabajo.
#
# AUDITORIA_INTEGRAL_PRODUCTO.md (hallazgo §2) encontró que el emparejamiento
# original exigía igualdad exacta de string contra la categoría real del
# proveedor -- "pisos" no calzaba con la categoría real de Construplaza,
# "Pisos y Enchapes", así que un material caía en "Sin partida" mientras un
# material equivalente de otro proveedor sí se clasificaba. Ahora se
# reconoce por palabra clave *contenida* en la categoría normalizada, no por
# igualdad -- verificado contra las categorías reales de los 4 proveedores
# integrados, no adivinado. Si ninguna palabra clave calza, queda "Sin
# partida" -- igual que hoy -- para no asignar mal por exceso de confianza.
# Es solo un valor inicial: el usuario lo puede cambiar en cualquier
# momento, igual que antes de este cambio.
PALABRAS_CLAVE_POR_PARTIDA_ID = {
    "electrico": ("electricidad", "electrico"),
    "hidraulico": ("plomeria", "fontaneria", "griferia", "hidraulico"),
    "pintura": ("pinturas", "pintura"),
    "estructura": ("construccion", "estructura"),
    "acabados": ("pisos", "enchape", "maderas y puertas", "acabado"),
}


def _sugerir_partida(categoria):
    if not categoria:
        return None
    categoria_normalizada = normalizar_texto(categoria)
    for partida_id, palabras_clave in PALABRAS_CLAVE_POR_PARTIDA_ID.items():
        if any(palabra in categoria_normalizada for palabra in palabras_clave):
            return _PARTIDA_POR_ID[partida_id].nombre
    return None


def _ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generar_token():
    return secrets.token_urlsafe(9)


def _calcular_totales(items):
    total_pendiente = 0
    total_comprado = 0

    for item in items:
        precio = item["precio_actual"]
        if precio is None:
            precio = item["precio_al_agregar"] or 0

        subtotal = item["cantidad"] * precio

        if item["estado"] == "comprado":
            total_comprado += subtotal
        elif item["estado"] != "descartado":
            total_pendiente += subtotal

    return round(total_pendiente), round(total_comprado)


# Cualquier partida que no esté en PARTIDAS_SUGERIDAS (texto libre del
# usuario) se ordena alfabéticamente después de estas, y "Sin partida"
# siempre queda de último -- es la señal visual de "esto todavía no lo he
# organizado". El orden en sí vive una sola vez, en PARTIDAS_SUGERIDAS
# arriba -- ya no hay una segunda lista separada que se pueda desincronizar.
SIN_PARTIDA = "Sin partida"


def _clave_orden_partida(nombre_partida):
    if nombre_partida == SIN_PARTIDA:
        return (2, "")
    if nombre_partida in _NOMBRES_PARTIDAS_SUGERIDAS:
        return (0, _NOMBRES_PARTIDAS_SUGERIDAS.index(nombre_partida))
    return (1, nombre_partida.lower())


def _agrupar_por_partida(items):
    """Agrupa los ítems de un proyecto (materiales, por ahora) en partidas
    para la cotización. Ítems descartados no forman parte de la cotización
    -- ya se excluyen en todo el resto del sistema (ver _calcular_totales).

    Devuelto como lista de dicts {partida, items, subtotal}, sin depender
    de nada específico de "material" más allá de cantidad/precio/partida --
    cuando exista mano de obra, sumarla a esta misma agrupación no debería
    requerir cambiar esta función, solo pasarle una lista de renglones que
    también tengan esos tres campos (ver COTIZACIONES_V1.md).

    Se agrupa por el texto de partida ya normalizado (sin tildes, sin
    mayúsculas) -- las partidas de la lista fija siempre coinciden entre
    sí, pero las de texto libre ("Otra...", ver SelectorPartida.tsx) no
    tenían ninguna protección: "Plomeria" y "Plomería" quedaban como dos
    secciones separadas, cada una con su propio subtotal parcial, sin
    ningún aviso de que la partida se había fragmentado. El nombre que se
    muestra es el de la primera vez que apareció esa partida (por
    fecha_agregado), para no imponer una ortografía distinta a la que el
    usuario ya venía usando."""

    grupos = {}

    for item in items:
        if item["estado"] == "descartado":
            continue

        precio = item["precio_actual"]
        if precio is None:
            precio = item["precio_al_agregar"] or 0

        nombre_partida = item["partida"] or SIN_PARTIDA
        clave = normalizar_texto(nombre_partida)
        grupo = grupos.setdefault(
            clave, {"partida": nombre_partida, "items": [], "subtotal": 0}
        )
        grupo["items"].append(item)
        grupo["subtotal"] += item["cantidad"] * precio

    for grupo in grupos.values():
        grupo["subtotal"] = round(grupo["subtotal"], 2)

    return sorted(grupos.values(), key=lambda g: _clave_orden_partida(g["partida"]))


def _calcular_cotizacion(proyecto, items):
    """Desglose completo de la cotización: partidas con subtotal, y encima
    de la suma de todas ellas, los tres porcentajes configurables del
    proyecto (indirectos, imprevistos, margen) aplicados como % planos
    sobre el mismo subtotal -- no en cascada uno sobre otro. Es una
    simplificación deliberada y documentada (ver COTIZACIONES_V1.md): hay
    firmas que sí calculan en cascada, pero un % plano y transparente sobre
    la misma base es más fácil de auditar a simple vista, y no había una
    única convención "correcta" que asumir sin inventar un criterio propio."""

    partidas = _agrupar_por_partida(items)
    subtotal_materiales = round(sum(p["subtotal"] for p in partidas), 2)

    indirectos = round(subtotal_materiales * (proyecto["indirectos_porcentaje"] or 0) / 100, 2)
    imprevistos = round(subtotal_materiales * (proyecto["imprevistos_porcentaje"] or 0) / 100, 2)
    margen = round(subtotal_materiales * (proyecto["margen_porcentaje"] or 0) / 100, 2)
    total_final = round(subtotal_materiales + indirectos + imprevistos + margen, 2)

    area_m2 = proyecto.get("area_m2")
    costo_por_m2 = round(total_final / area_m2, 2) if area_m2 else None

    return {
        "partidas": partidas,
        "subtotal_materiales": subtotal_materiales,
        "indirectos": indirectos,
        "imprevistos": imprevistos,
        "margen": margen,
        "total_final": total_final,
        "costo_por_m2": costo_por_m2,
    }


def _obtener_items(conexion, proyecto_id):
    cursor = conexion.execute(
        """
        SELECT
            i.id, i.proveedor, i.id_proveedor, i.cantidad, i.unidad_medida,
            i.estado, i.prioridad, i.comentario, i.fecha_agregado, i.partida,
            i.precio_al_agregar,
            i.origen, i.pagina_fuente, i.lamina_fuente, i.texto_original,
            i.confianza, i.regla_generadora,
            i.confianza_match, i.revisado,
            COALESCE(pr.nombre, i.nombre_al_agregar) AS nombre,
            COALESCE(pr.marca, i.marca_al_agregar) AS marca,
            COALESCE(pr.categoria, i.categoria_al_agregar) AS categoria,
            COALESCE(pr.url_imagen, i.url_imagen_al_agregar) AS url_imagen,
            COALESCE(pr.url_producto, i.url_producto_al_agregar) AS url_producto,
            pr.precio AS precio_actual,
            (pr.id IS NOT NULL) AS disponible
        FROM items_proyecto i
        LEFT JOIN productos pr
            ON pr.proveedor = i.proveedor AND pr.id_proveedor = i.id_proveedor
        WHERE i.proyecto_id = ?
        ORDER BY i.fecha_agregado ASC
        """,
        (proyecto_id,),
    )

    items = []
    for fila in cursor.fetchall():
        item = dict(fila)
        item["disponible"] = bool(item["disponible"])
        item["revisado"] = bool(item["revisado"])
        # Unidad de venta legible derivada del nombre (Galón, 25 kg,
        # 2.08 m²...) -- ver PRUEBA_INGENIERO_BANO.md, hallazgo #2. None
        # si no hay señal confiable; el frontend cae a "c/u" en ese caso.
        item["unidad_comercial"] = _unidad_comercial(item["nombre"], item["categoria"])
        items.append(item)

    return items


def crear_proyecto(propietario_id, nombre, comentario=None, fecha_objetivo=None):
    ahora = _ahora()
    token = _generar_token()

    conexion = conectar()
    cursor = conexion.execute(
        """
        INSERT INTO proyectos (
            propietario_id, nombre, comentario, estado,
            fecha_objetivo, token_compartido, fecha_creacion, fecha_actualizacion
        ) VALUES (?, ?, ?, 'activo', ?, ?, ?, ?)
        """,
        (propietario_id, nombre, comentario, fecha_objetivo, token, ahora, ahora),
    )
    proyecto_id = cursor.lastrowid
    conexion.commit()
    conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def listar_proyectos(propietario_id, incluir_archivados=False):
    conexion = conectar()

    condicion = "" if incluir_archivados else "AND p.estado != 'archivado'"

    cursor = conexion.execute(
        f"""
        SELECT
            p.id, p.nombre, p.cliente, p.estado, p.fecha_objetivo, p.fecha_actualizacion,
            COUNT(i.id) AS cantidad_items
        FROM proyectos p
        LEFT JOIN items_proyecto i
            ON i.proyecto_id = p.id AND i.estado != 'descartado'
        WHERE p.propietario_id = ? {condicion}
        GROUP BY p.id
        ORDER BY p.fecha_actualizacion DESC
        """,
        (propietario_id,),
    )

    resumenes = []
    for fila in cursor.fetchall():
        resumen = dict(fila)
        items = _obtener_items(conexion, resumen["id"])
        total_pendiente, total_comprado = _calcular_totales(items)
        resumen["total_pendiente"] = total_pendiente
        resumen["total_comprado"] = total_comprado
        resumenes.append(resumen)

    conexion.close()
    return resumenes


def obtener_proyecto(proyecto_id=None, propietario_id=None, token=None):
    conexion = conectar()

    if token is not None:
        fila = conexion.execute(
            "SELECT * FROM proyectos WHERE token_compartido = ?", (token,)
        ).fetchone()
    else:
        fila = conexion.execute(
            "SELECT * FROM proyectos WHERE id = ? AND propietario_id = ?",
            (proyecto_id, propietario_id),
        ).fetchone()

    if not fila:
        conexion.close()
        return None

    proyecto = dict(fila)
    items = _obtener_items(conexion, proyecto["id"])
    conexion.close()

    total_pendiente, total_comprado = _calcular_totales(items)

    proyecto["items"] = items
    proyecto["total_pendiente"] = total_pendiente
    proyecto["total_comprado"] = total_comprado
    proyecto["cotizacion"] = _calcular_cotizacion(proyecto, items)

    # mismo patrón que productos.imagenes_adicionales (ver api/main.py):
    # columna TEXT con JSON serializado a mano, no un tipo JSON nativo.
    if proyecto.get("plano_analisis") is not None:
        try:
            proyecto["plano_analisis"] = json.loads(proyecto["plano_analisis"])
        except ValueError:
            proyecto["plano_analisis"] = None

    return proyecto


def actualizar_proyecto(proyecto_id, propietario_id, cambios):
    campos_validos = {
        "nombre", "comentario", "estado", "fecha_objetivo",
        "cliente", "direccion", "area_m2",
        "indirectos_porcentaje", "imprevistos_porcentaje", "margen_porcentaje",
    }
    cambios = {k: v for k, v in cambios.items() if k in campos_validos and v is not None}

    if "estado" in cambios and cambios["estado"] not in ESTADOS_PROYECTO:
        raise ValueError(f"Estado inválido: {cambios['estado']}")

    if not cambios:
        return obtener_proyecto(proyecto_id, propietario_id=propietario_id)

    conexion = conectar()
    fila = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()

    if not fila:
        conexion.close()
        return None

    cambios["fecha_actualizacion"] = _ahora()
    asignaciones = ", ".join(f"{campo} = ?" for campo in cambios)

    conexion.execute(
        f"UPDATE proyectos SET {asignaciones} WHERE id = ?",
        (*cambios.values(), proyecto_id),
    )
    conexion.commit()
    conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def eliminar_proyecto(proyecto_id, propietario_id):
    conexion = conectar()
    fila = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()

    if not fila:
        conexion.close()
        return False

    conexion.execute("DELETE FROM proyectos WHERE id = ?", (proyecto_id,))
    conexion.commit()
    conexion.close()

    return True


def agregar_item(
    proyecto_id, propietario_id, proveedor, id_proveedor, cantidad=1,
    origen=None, pagina_fuente=None, lamina_fuente=None, texto_original=None,
    confianza=None, regla_generadora=None, unidad_medida=None,
    confianza_match=None, revisado=1,
):
    if origen is not None and origen not in ORIGENES_ITEM_VALIDOS:
        raise ValueError(f"Origen inválido: {origen}")

    conexion = conectar()

    proyecto = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()

    if not proyecto:
        conexion.close()
        return None

    producto = conexion.execute(
        """
        SELECT nombre, marca, categoria, precio, url_imagen, url_producto
        FROM productos
        WHERE proveedor = ? AND id_proveedor = ?
        """,
        (proveedor, id_proveedor),
    ).fetchone()

    if not producto:
        conexion.close()
        raise ValueError("Producto no encontrado en el catálogo")

    ahora = _ahora()
    partida_sugerida = _sugerir_partida(producto["categoria"])

    conexion.execute(
        """
        INSERT INTO items_proyecto (
            proyecto_id, proveedor, id_proveedor, cantidad,
            nombre_al_agregar, marca_al_agregar, categoria_al_agregar,
            precio_al_agregar, url_imagen_al_agregar, url_producto_al_agregar,
            fecha_agregado, partida,
            origen, pagina_fuente, lamina_fuente, texto_original,
            confianza, regla_generadora, unidad_medida,
            confianza_match, revisado
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(proyecto_id, proveedor, id_proveedor)
        DO UPDATE SET
            -- Si el ítem ya existía pendiente, sumar es lo esperado (agregar
            -- "2 más" de algo que ya estaba en la lista). Pero si estaba
            -- descartado o comprado, sumarle a esa cantidad vieja no tiene
            -- sentido -- el usuario está agregándolo de nuevo, no ajustando
            -- una cantidad activa. Antes de este fix, reagregar un ítem
            -- descartado no lo reactivaba (seguía sin aparecer en ningún
            -- total) sin ningún error visible.
            cantidad = CASE
                WHEN estado = 'pendiente' THEN cantidad + excluded.cantidad
                ELSE excluded.cantidad
            END,
            estado = 'pendiente'
            -- partida NO se toca aquí a propósito: si el ítem ya existía
            -- (reactivado desde descartado, por ejemplo), el usuario pudo
            -- haberla organizado a mano -- reagregarlo no debe pisarla.
            -- origen/pagina_fuente/lamina_fuente/texto_original/confianza/
            -- regla_generadora/unidad_medida TAMPOCO se tocan en conflicto,
            -- a propósito (AUDITORIA_INTEGRAL_PRODUCTO.md §1: "nunca debe
            -- perderse esa información") -- conservan el origen del primer
            -- agregado, nunca se sobrescriben ni se fusionan con un
            -- segundo origen. confianza_match/revisado: mismo criterio --
            -- no se tocan en conflicto, conservan lo que dejó el primer
            -- agregado (ver seleccion_automatica.py).
        """,
        (
            proyecto_id, proveedor, id_proveedor, cantidad,
            producto["nombre"], producto["marca"], producto["categoria"],
            producto["precio"], producto["url_imagen"], producto["url_producto"],
            ahora, partida_sugerida,
            origen, pagina_fuente, lamina_fuente, texto_original,
            confianza, regla_generadora, unidad_medida,
            confianza_match, revisado,
        ),
    )

    conexion.execute(
        "UPDATE proyectos SET fecha_actualizacion = ? WHERE id = ?",
        (ahora, proyecto_id),
    )

    # Un evento por cada llamada real a agregar_item(), sin importar el
    # origen -- si origen='plano' y confianza_match no es None, ESTE
    # evento ES la "sugerencia automática" que seleccion_automatica.py
    # acaba de generar (ver eventos.py, no hay un tipo de evento aparte
    # para eso). item_id se busca aparte porque el INSERT de arriba usa
    # ON CONFLICT DO UPDATE -- (proyecto_id, proveedor, id_proveedor) es
    # UNIQUE, así que esta lectura siempre identifica la fila correcta,
    # haya sido inserción o actualización.
    item_id = conexion.execute(
        "SELECT id FROM items_proyecto WHERE proyecto_id = ? AND proveedor = ? AND id_proveedor = ?",
        (proyecto_id, proveedor, id_proveedor),
    ).fetchone()["id"]
    _eventos.registrar_evento(
        conexion, _eventos.TIPO_ITEM_AGREGADO,
        usuario_id=propietario_id, proyecto_id=proyecto_id, item_id=item_id,
        proveedor=proveedor, id_proveedor=id_proveedor,
        categoria=producto["categoria"], origen=origen,
        confianza_match=confianza_match, texto_material=texto_original,
    )

    conexion.commit()
    conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def actualizar_item(proyecto_id, propietario_id, item_id, cambios):
    campos_validos = {"cantidad", "estado", "prioridad", "comentario", "partida", "revisado"}
    cambios = {k: v for k, v in cambios.items() if k in campos_validos and v is not None}

    if "estado" in cambios and cambios["estado"] not in ESTADOS_ITEM:
        raise ValueError(f"Estado inválido: {cambios['estado']}")

    conexion = conectar()

    proyecto = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()

    if not proyecto:
        conexion.close()
        return None

    # Leído ANTES del UPDATE, y solo cuando de verdad podría ser una
    # aceptación (revisado pasando a True) -- es la única forma de saber
    # si esto es una transición real de 0 a 1 (una "aceptación" de
    # verdad) o un PATCH redundante sobre un ítem que ya estaba
    # revisado=1 (nunca debe registrar un segundo evento por lo mismo).
    item_previo = None
    if cambios.get("revisado"):
        item_previo = conexion.execute(
            """
            SELECT revisado, origen, confianza_match, proveedor, id_proveedor,
                   categoria_al_agregar, texto_original, fecha_agregado
            FROM items_proyecto WHERE id = ? AND proyecto_id = ?
            """,
            (item_id, proyecto_id),
        ).fetchone()

    if cambios:
        asignaciones = ", ".join(f"{campo} = ?" for campo in cambios)
        cursor = conexion.execute(
            f"UPDATE items_proyecto SET {asignaciones} WHERE id = ? AND proyecto_id = ?",
            (*cambios.values(), item_id, proyecto_id),
        )
        # PRODUCTION_READINESS_REVIEW.md, hallazgo F3: si el ítem ya no
        # existe (otra pestaña ya lo borró, por ejemplo), el UPDATE de
        # arriba no toca ninguna fila -- sin este chequeo, la función
        # igual devolvía 200 con el proyecto tal cual, dando a entender
        # falsamente que el cambio se aplicó.
        if cursor.rowcount == 0:
            conexion.close()
            return None

        if (
            item_previo is not None
            and not item_previo["revisado"]
            and item_previo["origen"] == "plano"
            and item_previo["confianza_match"]
        ):
            _eventos.registrar_evento(
                conexion, _eventos.TIPO_SELECCION_ACEPTADA,
                usuario_id=propietario_id, proyecto_id=proyecto_id, item_id=item_id,
                proveedor=item_previo["proveedor"], id_proveedor=item_previo["id_proveedor"],
                categoria=item_previo["categoria_al_agregar"], origen=item_previo["origen"],
                confianza_match=item_previo["confianza_match"], texto_material=item_previo["texto_original"],
                tiempo_hasta_decision_segundos=_eventos.segundos_desde(item_previo["fecha_agregado"]),
            )

        conexion.execute(
            "UPDATE proyectos SET fecha_actualizacion = ? WHERE id = ?",
            (_ahora(), proyecto_id),
        )
        conexion.commit()

    conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def eliminar_item(proyecto_id, propietario_id, item_id):
    conexion = conectar()

    proyecto = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()

    if not proyecto:
        conexion.close()
        return None

    # Leído ANTES del DELETE -- después de borrar la fila ya no hay de
    # dónde sacar esta información (ver database/agregar_eventos.py:
    # eventos.py guarda todo lo necesario denormalizado justamente para
    # no depender de que items_proyecto siga existiendo).
    item_previo = conexion.execute(
        """
        SELECT revisado, origen, confianza_match, proveedor, id_proveedor,
               categoria_al_agregar, texto_original, fecha_agregado
        FROM items_proyecto WHERE id = ? AND proyecto_id = ?
        """,
        (item_id, proyecto_id),
    ).fetchone()

    cursor = conexion.execute(
        "DELETE FROM items_proyecto WHERE id = ? AND proyecto_id = ?",
        (item_id, proyecto_id),
    )
    # PRODUCTION_READINESS_REVIEW.md, hallazgo F3: mismo chequeo que
    # actualizar_item -- un DELETE que no borró nada (ítem ya no existía)
    # no debe reportarse como éxito.
    if cursor.rowcount == 0:
        conexion.close()
        return None

    if item_previo is not None:
        # Un ítem sugerido por el motor que todavía estaba pendiente de
        # revisión y se descarta SIN reemplazo es una señal real de
        # "el motor se equivocó" (seleccion_eliminada) -- cualquier otro
        # borrado (ya revisado, o de origen manual) es solo un borrado
        # común (item_eliminado), no dice nada sobre la calidad de una
        # sugerencia automática.
        es_rechazo_de_sugerencia = (
            not item_previo["revisado"]
            and item_previo["origen"] == "plano"
            and bool(item_previo["confianza_match"])
        )
        _eventos.registrar_evento(
            conexion,
            _eventos.TIPO_SELECCION_ELIMINADA if es_rechazo_de_sugerencia else _eventos.TIPO_ITEM_ELIMINADO,
            usuario_id=propietario_id, proyecto_id=proyecto_id, item_id=item_id,
            proveedor=item_previo["proveedor"], id_proveedor=item_previo["id_proveedor"],
            categoria=item_previo["categoria_al_agregar"], origen=item_previo["origen"],
            confianza_match=item_previo["confianza_match"], texto_material=item_previo["texto_original"],
            tiempo_hasta_decision_segundos=(
                _eventos.segundos_desde(item_previo["fecha_agregado"]) if es_rechazo_de_sugerencia else None
            ),
        )

    conexion.execute(
        "UPDATE proyectos SET fecha_actualizacion = ? WHERE id = ?",
        (_ahora(), proyecto_id),
    )
    conexion.commit()
    conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def reemplazar_item(proyecto_id, propietario_id, item_id, proveedor_nuevo, id_proveedor_nuevo, cantidad=None):
    """Reemplaza un ítem por otro elegido a mano -- combina eliminar +
    agregar_item(origen='manual') en una sola operación, para poder
    registrar UN evento 'seleccion_reemplazada' con el producto ANTERIOR
    (lo que sugirió el motor) y el NUEVO (lo que eligió el usuario) en la
    misma fila. Dos llamadas separadas (eliminar_item + agregar_item, que
    es como funcionaba el frontend antes de esto) no permiten reconstruir
    esa relación de forma confiable si hay varias decisiones pendientes
    al mismo tiempo -- no hay ninguna clave que diga "este alta reemplaza
    a esa baja en particular".

    `cantidad`, si no se da, conserva la cantidad del ítem reemplazado."""

    conexion = conectar()

    proyecto = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()
    if not proyecto:
        conexion.close()
        return None

    item_previo = conexion.execute(
        """
        SELECT cantidad, revisado, origen, confianza_match, proveedor, id_proveedor,
               categoria_al_agregar, texto_original, fecha_agregado
        FROM items_proyecto WHERE id = ? AND proyecto_id = ?
        """,
        (item_id, proyecto_id),
    ).fetchone()
    if item_previo is None:
        conexion.close()
        return None

    cantidad_final = cantidad if cantidad is not None else item_previo["cantidad"]

    cursor = conexion.execute(
        "DELETE FROM items_proyecto WHERE id = ? AND proyecto_id = ?", (item_id, proyecto_id)
    )
    if cursor.rowcount == 0:
        conexion.close()
        return None

    conexion.execute(
        "UPDATE proyectos SET fecha_actualizacion = ? WHERE id = ?", (_ahora(), proyecto_id)
    )
    conexion.commit()
    conexion.close()

    # Reutiliza agregar_item() en vez de duplicar su lógica de guardado
    # (mismo criterio que ya usa generar_cotizacion_automatica) -- esto
    # también registra su propio evento item_agregado (origen='manual'),
    # que es correcto y esperado: es un alta real, independiente del
    # evento de reemplazo que se registra abajo.
    proyecto_actualizado = agregar_item(
        proyecto_id, propietario_id, proveedor_nuevo, id_proveedor_nuevo,
        cantidad=cantidad_final, origen="manual",
    )
    if proyecto_actualizado is None:
        return None

    es_reemplazo_de_sugerencia = (
        not item_previo["revisado"]
        and item_previo["origen"] == "plano"
        and bool(item_previo["confianza_match"])
    )

    conexion = conectar()
    _eventos.registrar_evento(
        conexion, _eventos.TIPO_SELECCION_REEMPLAZADA,
        usuario_id=propietario_id, proyecto_id=proyecto_id, item_id=item_id,
        proveedor=proveedor_nuevo, id_proveedor=id_proveedor_nuevo,
        proveedor_anterior=item_previo["proveedor"], id_proveedor_anterior=item_previo["id_proveedor"],
        categoria=item_previo["categoria_al_agregar"], origen=item_previo["origen"],
        confianza_match=item_previo["confianza_match"], texto_material=item_previo["texto_original"],
        tiempo_hasta_decision_segundos=(
            _eventos.segundos_desde(item_previo["fecha_agregado"]) if es_reemplazo_de_sugerencia else None
        ),
    )
    conexion.commit()
    conexion.close()

    return proyecto_actualizado


def analizar_plano(proyecto_id, propietario_id, ruta_pdf, nombre_archivo):
    """Corre lectura_planos sobre un PDF ya guardado en disco (ver el
    router: el archivo subido se escribe a un temporal antes de llamar
    acá) y guarda el resultado -- niveles, espacios, cuadros de
    puertas/ventanas/acabados, cómputo estructural y las láminas que
    todos ellos referencian -- como JSON en la fila del proyecto (ver
    FLUJO_PRESUPUESTO_DESDE_PLANO_V1.md). El PDF en sí no se guarda, solo
    el análisis ya estructurado."""
    conexion = conectar()
    fila = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()

    if not fila:
        conexion.close()
        return None

    # .result() bloquea ESTE hilo hasta que el proceso hijo termine, pero
    # bloquear un hilo esperando a otro proceso es una espera de I/O (un
    # pipe), no trabajo de CPU -- libera el GIL mientras espera, que es
    # justo lo que permite que el resto de la app siga respondiendo
    # mientras tanto (ver INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md).
    analisis = _EXECUTOR_PLANOS.submit(_procesar_plano_pdf, ruta_pdf).result()

    ahora = _ahora()
    conexion.execute(
        """
        UPDATE proyectos
        SET plano_nombre_archivo = ?, plano_analisis = ?, plano_fecha_analisis = ?, fecha_actualizacion = ?
        WHERE id = ?
        """,
        (nombre_archivo, json.dumps(analisis), ahora, ahora, proyecto_id),
    )
    conexion.commit()
    conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def eliminar_plano(proyecto_id, propietario_id):
    conexion = conectar()
    fila = conexion.execute(
        "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()

    if not fila:
        conexion.close()
        return None

    conexion.execute(
        """
        UPDATE proyectos
        SET plano_nombre_archivo = NULL, plano_analisis = NULL, plano_fecha_analisis = NULL, fecha_actualizacion = ?
        WHERE id = ?
        """,
        (_ahora(), proyecto_id),
    )
    conexion.commit()
    conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def generar_cotizacion_automatica(proyecto_id, propietario_id):
    """Corre seleccion_automatica.py sobre cada material del plano ya
    analizado (puertas, ventanas, acabados, piezas estructurales) y agrega
    al proyecto -- vía agregar_item(), sin duplicar su lógica de guardado
    -- los que el motor logró emparejar con un producto real del catálogo,
    con confianza alta/media/baja. Los que no logró emparejar quedan
    exactamente como hoy: disponibles para agregar a mano desde "Materiales
    encontrados" (MaterialesDelPlano.tsx) -- este flujo nunca los toca ni
    los oculta.

    Cada ítem que sí se agrega nace con revisado=0 -- pendiente de que un
    humano lo apruebe, reemplace o elimine antes de dar la cotización por
    buena (ver items_proyecto.revisado, agregar_seleccion_automatica.py).

    No intenta evitar volver a intentar un material ya cubierto en una
    corrida anterior -- agregar_item() ya es idempotente por (proyecto_id,
    proveedor, id_proveedor) vía ON CONFLICT, así que reintentar un
    material que ya resultó en el mismo producto no duplica nada; si el
    motor eligiera un producto DISTINTO en una corrida posterior (el
    catálogo cambió, por ejemplo), sí podría dejar dos ítems similares --
    aceptado como límite conocido de esta primera versión."""

    conexion = conectar()
    fila = conexion.execute(
        "SELECT plano_analisis FROM proyectos WHERE id = ? AND propietario_id = ?",
        (proyecto_id, propietario_id),
    ).fetchone()
    conexion.close()

    if not fila:
        return None

    if not fila["plano_analisis"]:
        raise ValueError("Este proyecto todavía no tiene un plano analizado.")

    analisis = json.loads(fila["plano_analisis"])

    resumen = {"agregados": 0, "sin_seleccion": 0}

    for puerta in analisis.get("puertas", []):
        _procesar_material_plano(
            proyecto_id, propietario_id, puerta,
            _seleccionar_para_puerta_ventana(puerta, es_ventana=False),
            regla_generadora="cuadro_puertas", cantidad=puerta.get("cantidad") or 1,
            resumen=resumen,
        )

    for ventana in analisis.get("ventanas", []):
        _procesar_material_plano(
            proyecto_id, propietario_id, ventana,
            _seleccionar_para_puerta_ventana(ventana, es_ventana=True),
            regla_generadora="cuadro_ventanas", cantidad=ventana.get("cantidad") or 1,
            resumen=resumen,
        )

    for acabado in analisis.get("acabados", []):
        _procesar_material_plano(
            proyecto_id, propietario_id, acabado,
            _seleccionar_para_acabado(acabado),
            regla_generadora="cuadro_acabados", cantidad=1,
            resumen=resumen,
        )

    for pieza in analisis.get("piezas_estructurales", []):
        _procesar_material_plano(
            proyecto_id, propietario_id, pieza,
            _seleccionar_para_pieza_estructural(pieza),
            regla_generadora="computo_estructural", cantidad=pieza.get("cantidad") or 1,
            resumen=resumen,
        )

    proyecto = obtener_proyecto(proyecto_id, propietario_id=propietario_id)
    proyecto["cotizacion_automatica_resumen"] = resumen
    return proyecto


def _procesar_material_plano(proyecto_id, propietario_id, material, seleccion, regla_generadora, cantidad, resumen):
    if seleccion["producto"] is None:
        resumen["sin_seleccion"] += 1
        return

    producto = seleccion["producto"]
    agregar_item(
        proyecto_id, propietario_id, producto["proveedor"], producto["id_proveedor"],
        cantidad=cantidad,
        origen="plano",
        pagina_fuente=material.get("pagina_fuente"),
        texto_original=material.get("texto_original"),
        confianza=material.get("confianza"),
        regla_generadora=regla_generadora,
        confianza_match=seleccion["confianza"],
        revisado=0,
    )
    resumen["agregados"] += 1
