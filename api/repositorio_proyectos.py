import functools
import json
import math
import multiprocessing
import os
import secrets
import signal
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from db import conectar
from busqueda import normalizar_texto
from especificaciones import unidad_comercial as _unidad_comercial
from api.observabilidad import logger as _logger, id_de_peticion_actual as _id_peticion
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
_LOCK_EXECUTOR_PLANOS = threading.Lock()

# Mission #002 (Plan Processing Stability): verificado directo contra
# producción -- un plano de 105MB (35% del límite de TAMANO_MAXIMO_
# PLANO_BYTES) ya devuelve 502 a los 43s hoy (ver HANDOFF de esta
# misión), muy por debajo de cualquier timeout que se le pudiera poner a
# .result(). Un timeout interno por sí solo no alcanza -- lo que hace
# falta es que la petición HTTP nunca dependa de que el análisis termine.
TIMEOUT_ANALISIS_SEGUNDOS = 120

MENSAJE_ERROR_GENERICO = "No se pudo leer el PDF. Verificá que no esté dañado o protegido."
MENSAJE_ERROR_TIMEOUT = "El análisis tardó demasiado. Probá con un archivo más liviano o contactá soporte."
MENSAJE_ERROR_INTERRUPCION = "El proceso se interrumpió (reinicio del servidor). Subí el plano de nuevo."


class AnalisisPlanoEnCurso(Exception):
    """El usuario ya tiene un análisis de plano corriendo (en este u otro
    proyecto) -- ver _reclamar_slot_analisis. Mapea a 429 en el router."""


# token de plano_procesamiento_id -> threading.Timer. Solo vive en memoria
# de ESTE proceso a propósito: si el proceso se reinicia, cualquier timer
# pendiente desaparece con él, pero eso está bien -- recuperar_analisis_
# interrumpidos() (llamado al arrancar, ver api/main.py) ya cubre ese
# caso marcando 'error' cualquier plano_estado='procesando' huérfano.
_TEMPORIZADORES_ANALISIS = {}
_LOCK_TEMPORIZADORES = threading.Lock()


def _armar_watchdog(proyecto_id, token):
    temporizador = threading.Timer(
        TIMEOUT_ANALISIS_SEGUNDOS, _manejar_timeout_analisis, args=(proyecto_id, token)
    )
    temporizador.daemon = True
    with _LOCK_TEMPORIZADORES:
        _TEMPORIZADORES_ANALISIS[token] = temporizador
    temporizador.start()


def _cancelar_watchdog(token):
    with _LOCK_TEMPORIZADORES:
        temporizador = _TEMPORIZADORES_ANALISIS.pop(token, None)
    if temporizador is not None:
        temporizador.cancel()


def _reciclar_executor_planos():
    """Best-effort, no una garantía -- ver HANDOFF de Mission #002 para
    la verificación empírica que sustenta este diseño.

    Verificado directo (no en la documentación de la librería estándar,
    que no lo aclara): ProcessPoolExecutor.shutdown(wait=False,
    cancel_futures=True) NO mata un proceso hijo que ya está corriendo --
    cancela únicamente trabajo todavía encolado sin empezar, y detiene el
    hilo administrador para que no acepte trabajo nuevo. El proceso hijo
    atascado sigue vivo y consumiendo su núcleo/memoria hasta que algo lo
    mate de verdad.

    Por eso acá se rastrea el PID real del proceso hijo (ejecutor.
    _processes -- atributo interno, no público, de ProcessPoolExecutor;
    no hay alternativa pública en la librería estándar para este caso
    puntual) y se le manda SIGKILL directo, verificado empíricamente que
    sí lo mata. `_processes` se lee con getattr() por las dudas: si una
    versión futura de Python le cambia el nombre, esto no debe romper el
    resto del reciclaje -- en el peor caso, no se manda el SIGKILL, pero
    igual se arma un pool nuevo y se sigue aceptando trabajo nuevo."""
    global _EXECUTOR_PLANOS

    with _LOCK_EXECUTOR_PLANOS:
        viejo = _EXECUTOR_PLANOS
        pids = [proceso.pid for proceso in getattr(viejo, "_processes", {}).values()]

        viejo.shutdown(wait=False, cancel_futures=True)

        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
                _logger.warning(f"ANALISIS_PLANO worker_matado pid={pid} causa=timeout")
            except ProcessLookupError:
                pass
            except Exception:
                _logger.exception(f"ANALISIS_PLANO no_se_pudo_matar_worker pid={pid}")

        _EXECUTOR_PLANOS = ProcessPoolExecutor(max_workers=1, mp_context=_CONTEXTO_PROCESOS)


def apagar_executor_planos():
    """Lee el executor ACTUAL (no una referencia guardada al importar --
    _reciclar_executor_planos() puede haber reemplazado el objeto en
    cualquier momento, ver arriba) y lo apaga. Llamado desde el shutdown
    de la app (ver api/main.py)."""
    _EXECUTOR_PLANOS.shutdown(wait=False, cancel_futures=True)


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
# "parcial" (ver COMPRAS.md) se llega solo a través de registrar_compra_item
# -- no es una opción que el selector genérico de estado del frontend
# ofrezca a mano, porque sin una cantidad_comprada real asociada no
# significa nada. Queda en el set igual porque actualizar_item() valida
# contra él, y un valor que la propia app puede escribir nunca debería
# rechazar su propia escritura.
ESTADOS_ITEM = {"pendiente", "parcial", "comprado", "descartado"}

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
    """total_comprado es también el "gasto real" que lee Control de
    Costos (ver CONTROL_DE_COSTOS.md) -- esta sigue siendo la ÚNICA
    función que lo calcula, Compras (ver COMPRAS.md) no agrega una
    segunda. Con Compras, un ítem "parcial" divide su costo entre lo ya
    gastado (monto_comprado real si se registró uno, o cantidad_comprada
    × precio si no) y lo que falta por comprar (el resto, a precio de
    catálogo) -- exactamente el mismo criterio de fallback
    (monto real > estimado por precio) que ya usaba "comprado" antes de
    Compras, solo que ahora también se aplica a la porción parcial."""

    total_pendiente = 0
    total_comprado = 0

    for item in items:
        if item["estado"] == "descartado":
            continue

        precio = item["precio_actual"]
        if precio is None:
            precio = item["precio_al_agregar"] or 0

        if item["estado"] == "comprado":
            monto = item.get("monto_comprado")
            total_comprado += monto if monto is not None else item["cantidad"] * precio
        elif item["estado"] == "parcial":
            monto = item.get("monto_comprado")
            cantidad_comprada = item.get("cantidad_comprada") or 0
            total_comprado += monto if monto is not None else cantidad_comprada * precio
            total_pendiente += max(item["cantidad"] - cantidad_comprada, 0) * precio
        else:
            total_pendiente += item["cantidad"] * precio

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
            i.cantidad_comprada, i.monto_comprado, i.fecha_compra,
            i.comprobante_tipo, i.comprobante_referencia,
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
    # RELEASE_CANDIDATE.md (rendimiento): antes hacía 1 consulta para la
    # lista + 1 consulta extra POR proyecto (vía _obtener_items, con su
    # propio JOIN a productos) solo para sumar total_pendiente/
    # total_comprado -- un N+1 clásico. Es una sola consulta agregada,
    # pero el criterio de cada CASE tiene que replicar _calcular_totales()
    # exactamente -- incluyendo 'parcial' y el monto real registrado -- no
    # solo los tres estados que existían cuando se escribió el N+1 fix.
    #
    # INVESTIGACION_TOTAL_COMPRADO_INCONSISTENTE.md: la versión anterior
    # solo sumaba estado='comprado' a precio de catálogo ACTUAL, ignorando
    # 'parcial' por completo y descartando cualquier monto_comprado real
    # ya registrado -- listar_proyectos() y obtener_proyecto() (que sí usa
    # _calcular_totales()) podían devolver cifras muy distintas para la
    # misma obra. _calcular_totales() es la fuente de verdad (su propio
    # docstring ya lo decía: "la ÚNICA función que lo calcula"); acá se
    # replica su lógica en SQL, no al revés, porque monto_comprado es la
    # plata real que se pagó y no debe recalcularse contra un precio de
    # catálogo que pudo cambiar desde la compra.
    #
    # Cubierto por test_listar_proyectos_totales_con_estados_y_precios_mixtos
    # (casos originales) y test_listar_proyectos_coincide_con_detalle_* (
    # 'parcial', monto real, varias compras parciales sobre el mismo ítem,
    # compra sin monto explícito, obra sin compras) -- estas últimas
    # comparan directamente contra _calcular_totales() vía obtener_proyecto()
    # para que un futuro cambio en cualquiera de las dos implementaciones
    # que las desincronice haga fallar la prueba, no silenciosamente.
    conexion = conectar()

    condicion = "" if incluir_archivados else "AND p.estado != 'archivado'"

    cursor = conexion.execute(
        f"""
        SELECT
            p.id, p.nombre, p.cliente, p.estado, p.fecha_objetivo, p.fecha_actualizacion,
            COUNT(CASE WHEN i.estado != 'descartado' THEN i.id END) AS cantidad_items,
            COALESCE(SUM(
                CASE
                    WHEN i.estado = 'pendiente'
                    THEN i.cantidad * COALESCE(pr.precio, i.precio_al_agregar, 0)
                    WHEN i.estado = 'parcial'
                    THEN MAX(i.cantidad - i.cantidad_comprada, 0) * COALESCE(pr.precio, i.precio_al_agregar, 0)
                END
            ), 0) AS total_pendiente,
            COALESCE(SUM(
                CASE
                    WHEN i.estado = 'comprado'
                    THEN COALESCE(i.monto_comprado, i.cantidad * COALESCE(pr.precio, i.precio_al_agregar, 0))
                    WHEN i.estado = 'parcial'
                    THEN COALESCE(i.monto_comprado, i.cantidad_comprada * COALESCE(pr.precio, i.precio_al_agregar, 0))
                END
            ), 0) AS total_comprado
        FROM proyectos p
        LEFT JOIN items_proyecto i ON i.proyecto_id = p.id
        LEFT JOIN productos pr ON pr.proveedor = i.proveedor AND pr.id_proveedor = i.id_proveedor
        WHERE p.propietario_id = ? {condicion}
        GROUP BY p.id
        ORDER BY p.fecha_actualizacion DESC
        """,
        (propietario_id,),
    )

    resumenes = []
    for fila in cursor.fetchall():
        resumen = dict(fila)
        # _calcular_totales() redondea con round() de Python (bankers'
        # rounding) al final de la suma -- se replica acá para que el
        # resultado sea idéntico al de la versión anterior, no solo
        # "equivalente" (SUM ya viene en float desde SQLite).
        resumen["total_pendiente"] = round(resumen["total_pendiente"])
        resumen["total_comprado"] = round(resumen["total_comprado"])
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

    # Token interno de Mission #002 (ver iniciar_analisis_plano) -- solo
    # existe para que un resultado/timeout tardío de un intento viejo no
    # pise uno más nuevo. No le sirve al cliente para nada, no se expone.
    proyecto.pop("plano_procesamiento_id", None)

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


# --- Control de Costos (ver CONTROL_DE_COSTOS.md) -----------------------
#
# "Congelar" el presupuesto guarda una foto de _calcular_cotizacion() en
# una fila nueva de presupuesto_congelado -- nunca se sobreescribe una
# línea base anterior (ver database/agregar_control_costos.py). La línea
# base ACTIVA es, por diseño, la más reciente de un proyecto. El gasto
# real reutiliza total_comprado tal cual ya lo calcula _calcular_totales
# (el mismo número que ya se muestra en el resumen de cotización) -- este
# módulo no introduce una segunda forma de calcular gasto, solo lo
# compara contra una línea base.


def _resumen_partidas_para_congelar(partidas):
    """Versión liviana de cada partida para guardar en el snapshot --
    nombre/cantidad/precio unitario de cada ítem, no la fila completa
    (imágenes, urls, trazabilidad) que _calcular_cotizacion() trae para
    la vista en vivo y que acá no aporta nada a una comparación histórica
    de montos."""

    resumen = []
    for grupo in partidas:
        items_resumidos = []
        for item in grupo["items"]:
            precio = item["precio_actual"]
            if precio is None:
                precio = item["precio_al_agregar"] or 0
            items_resumidos.append({
                "nombre": item["nombre"],
                "cantidad": item["cantidad"],
                "precio_unitario": precio,
            })
        resumen.append({
            "partida": grupo["partida"],
            "subtotal": grupo["subtotal"],
            "items": items_resumidos,
        })
    return resumen


def congelar_presupuesto(proyecto_id, propietario_id):
    """Guarda la cotización actual del proyecto como línea base de Control
    de Costos. Devuelve el control de costos resultante (equivalente a
    llamar obtener_control_costos() justo después), o None si el proyecto
    no existe o no pertenece a propietario_id."""

    proyecto = obtener_proyecto(proyecto_id, propietario_id=propietario_id)
    if proyecto is None:
        return None

    cotizacion = proyecto["cotizacion"]

    conexion = conectar()
    conexion.execute(
        """
        INSERT INTO presupuesto_congelado (
            proyecto_id, fecha_creacion, subtotal_materiales,
            indirectos, imprevistos, margen, total_final, snapshot_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            proyecto_id,
            _ahora(),
            cotizacion["subtotal_materiales"],
            cotizacion["indirectos"],
            cotizacion["imprevistos"],
            cotizacion["margen"],
            cotizacion["total_final"],
            json.dumps(_resumen_partidas_para_congelar(cotizacion["partidas"]), ensure_ascii=False),
        ),
    )
    conexion.commit()
    conexion.close()

    return obtener_control_costos(proyecto_id, propietario_id)


def obtener_control_costos(proyecto_id, propietario_id):
    """Compara la línea base congelada más reciente (si existe) contra el
    gasto real acumulado hoy. Sin línea base todavía, devuelve
    tiene_linea_base=False y solo el gasto real -- nunca se inventa un
    "presupuestado" de cero para poder mostrar una comparación."""

    proyecto = obtener_proyecto(proyecto_id, propietario_id=propietario_id)
    if proyecto is None:
        return None

    gasto_real = proyecto["total_comprado"]

    conexion = conectar()
    fila = conexion.execute(
        """
        SELECT id, fecha_creacion, subtotal_materiales, indirectos,
               imprevistos, margen, total_final, snapshot_json
        FROM presupuesto_congelado
        WHERE proyecto_id = ?
        ORDER BY fecha_creacion DESC, id DESC
        LIMIT 1
        """,
        (proyecto_id,),
    ).fetchone()
    conexion.close()

    if fila is None:
        return {
            "tiene_linea_base": False,
            "linea_base": None,
            "gasto_real": gasto_real,
            "saldo_disponible": None,
            "porcentaje_ejecutado": None,
            "estado": None,
        }

    linea_base = dict(fila)
    snapshot_bruto = linea_base.pop("snapshot_json")
    try:
        linea_base["partidas"] = json.loads(snapshot_bruto)
    except (ValueError, TypeError):
        linea_base["partidas"] = []

    total_presupuestado = linea_base["total_final"]
    saldo_disponible = round(total_presupuestado - gasto_real, 2)
    porcentaje_ejecutado = round((gasto_real / total_presupuestado) * 100, 2) if total_presupuestado else 0

    if gasto_real > total_presupuestado:
        estado = "excedido"
    elif total_presupuestado and porcentaje_ejecutado >= 90:
        estado = "por_agotarse"
    else:
        estado = "en_curso"

    return {
        "tiene_linea_base": True,
        "linea_base": linea_base,
        "gasto_real": gasto_real,
        "saldo_disponible": saldo_disponible,
        "porcentaje_ejecutado": porcentaje_ejecutado,
        "estado": estado,
    }


# --- Compras (ver COMPRAS.md) --------------------------------------------
#
# Agrupa lo que todavía falta comprar por proveedor (para poder pedirlo
# todo de una ferretería de una vez), genera un documento de orden de
# compra por proveedor, y registra compras reales -- totales o
# parciales -- contra cada ítem. El gasto real sigue viviendo
# EXCLUSIVAMENTE en _calcular_totales() (ver su docstring, actualizado
# para "parcial"): Compras nunca calcula un segundo total de gasto, solo
# escribe los datos (cantidad_comprada/monto_comprado/estado) que esa
# función ya sabe leer -- así Control de Costos se actualiza solo, sin
# que este módulo tenga que llamarlo ni saber que existe.


def _agrupar_por_proveedor(items):
    """Solo ítems con algo pendiente de comprar (pendiente o parcial) --
    lo ya comprado no tiene nada que hacer en una orden de compra nueva,
    y lo descartado ya se excluye en todo el resto del sistema. El
    subtotal es sobre la cantidad TODAVÍA pendiente (cantidad -
    cantidad_comprada), no la cantidad total del ítem -- una orden de
    compra pide lo que falta, no lo que ya se compró."""

    grupos = {}

    for item in items:
        if item["estado"] not in ("pendiente", "parcial"):
            continue

        cantidad_pendiente = max(item["cantidad"] - (item.get("cantidad_comprada") or 0), 0)
        if cantidad_pendiente <= 0:
            continue

        precio = item["precio_actual"]
        if precio is None:
            precio = item["precio_al_agregar"] or 0

        grupo = grupos.setdefault(
            item["proveedor"], {"proveedor": item["proveedor"], "items": [], "subtotal": 0}
        )
        grupo["items"].append({**item, "cantidad_pendiente": cantidad_pendiente})
        grupo["subtotal"] += cantidad_pendiente * precio

    for grupo in grupos.values():
        grupo["subtotal"] = round(grupo["subtotal"], 2)

    return sorted(grupos.values(), key=lambda g: g["proveedor"])


def obtener_compras(proyecto_id, propietario_id):
    """Materiales pendientes agrupados por proveedor + historial de
    órdenes de compra ya generadas (más reciente primero)."""

    proyecto = obtener_proyecto(proyecto_id, propietario_id=propietario_id)
    if proyecto is None:
        return None

    conexion = conectar()
    filas = conexion.execute(
        """
        SELECT id, proveedor, numero, fecha_creacion, monto_total, snapshot_json
        FROM ordenes_compra
        WHERE proyecto_id = ?
        ORDER BY fecha_creacion DESC, id DESC
        """,
        (proyecto_id,),
    ).fetchall()
    conexion.close()

    ordenes = []
    for fila in filas:
        orden = dict(fila)
        snapshot_bruto = orden.pop("snapshot_json")
        try:
            orden["items"] = json.loads(snapshot_bruto)
        except (ValueError, TypeError):
            orden["items"] = []
        ordenes.append(orden)

    return {
        "pendientes_por_proveedor": _agrupar_por_proveedor(proyecto["items"]),
        "ordenes_generadas": ordenes,
    }


def _mayor_sufijo_numero_valido(conexion, proyecto_id):
    """Mayor sufijo numérico de `ordenes_compra.numero` ya usado por este
    proyecto, siguiendo exactamente el formato `OC-{proyecto_id}-{n}`.
    Cualquier fila cuyo `numero` no siga ese formato exacto (no debería
    ocurrir con datos generados por esta misma aplicación, pero nunca se
    asume) se ignora para el cálculo -- se registra únicamente la
    CANTIDAD ignorada, nunca el valor de `numero` en sí ni ningún otro
    dato comercial de la orden, para no filtrar información de negocio a
    los logs. 0 si no hay ninguna fila válida -- el próximo número
    empieza en 1."""

    mayor = 0
    ignorados = 0
    for (numero,) in conexion.execute(
        "SELECT numero FROM ordenes_compra WHERE proyecto_id = ?", (proyecto_id,)
    ):
        prefijo = f"OC-{proyecto_id}-"
        sufijo = (
            numero[len(prefijo):]
            if isinstance(numero, str) and numero.startswith(prefijo)
            else None
        )
        if (
            not sufijo
            or not sufijo.isascii()
            or not sufijo.isdecimal()
        ):
            ignorados += 1
            continue
        try:
            valor = int(sufijo)
        except (TypeError, ValueError):
            # También cubre límites defensivos del intérprete para cadenas
            # de miles de dígitos. Un valor histórico extraño nunca debe
            # impedir generar una orden nueva.
            ignorados += 1
            continue
        mayor = max(mayor, valor)

    if ignorados:
        _logger.info(
            "ORDEN_COMPRA_NUMERO_NO_ESTANDAR_IGNORADO proyecto_id=%s cantidad=%s",
            proyecto_id,
            ignorados,
        )
    return mayor


def generar_orden_compra(proyecto_id, propietario_id, proveedor):
    """Genera un documento de orden de compra nuevo con TODO lo
    pendiente de `proveedor` en este momento -- inmutable una vez creado
    (mismo criterio que presupuesto_congelado: un evento histórico, no un
    estado que se edita). Generar una orden no cambia el estado de
    ningún ítem -- es un documento para enviarle al proveedor, no un
    registro de que ya se compró (eso lo hace registrar_compra_item).

    AUDITORIA_COMPRAS_P0.md, hallazgo P0-1 (corregido, sin cambio de
    esquema): el número ya NO se calcula con `SELECT COUNT(*)` fuera de
    cualquier lock de escritura -- calcular el mayor sufijo existente e
    insertar la orden nueva corren dentro de la MISMA transacción
    `BEGIN IMMEDIATE`. SQLite solo permite un escritor a la vez y
    `BEGIN IMMEDIATE` toma ese lock de inmediato (no en el primer
    INSERT), así que dos llamadas concurrentes quedan serializadas por
    el propio motor: la segunda espera a que la primera termine (commit
    o rollback) antes de leer el máximo, así que ve el número que la
    primera ya insertó y nunca puede repetirlo.

    Riesgo residual documentado a propósito: esta función por sí sola
    hace que dos llamadas a través de ella nunca choquen, pero la base
    de datos en sí NO tiene ningún `UNIQUE INDEX` sobre
    `(proyecto_id, numero)` -- una escritura externa que no pase por
    esta función (una migración futura, un script a mano, un bug en
    otro código que inserte directo en `ordenes_compra`) podría insertar
    un duplicado sin que SQLite lo impida. Esa garantía de base de datos
    -- el índice único -- se evaluó en la revisión anterior y se dejó
    fuera de este hotfix a propósito, para un PR de esquema posterior
    (después del runner atómico de PR #1), que además decide cómo
    aplicarla de forma segura ante datos ya duplicados en producción."""

    conexion = conectar()
    try:
        conexion.execute("BEGIN IMMEDIATE")
        proyecto = conexion.execute(
            "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
            (proyecto_id, propietario_id),
        ).fetchone()
        if proyecto is None:
            conexion.rollback()
            return None

        # La vista comercial completa se obtiene después de tomar el write
        # lock. Así una compra que confirmó antes del lock ya está reflejada
        # en pendientes, cantidades, precios y subtotal de esta orden.
        items = _obtener_items(conexion, proyecto_id)
        grupo = next(
            (
                grupo
                for grupo in _agrupar_por_proveedor(items)
                if grupo["proveedor"] == proveedor
            ),
            None,
        )
        if grupo is None:
            raise ValueError(
                f"No hay materiales pendientes de {proveedor} en este proyecto"
            )

        items_snapshot = [
            {
                "nombre": item["nombre"],
                "cantidad": item["cantidad_pendiente"],
                "precio_unitario": (
                    item["precio_actual"]
                    if item["precio_actual"] is not None
                    else (item["precio_al_agregar"] or 0)
                ),
            }
            for item in grupo["items"]
        ]

        siguiente = _mayor_sufijo_numero_valido(conexion, proyecto_id) + 1
        numero = f"OC-{proyecto_id}-{siguiente}"
        conexion.execute(
            """
            INSERT INTO ordenes_compra (
                proyecto_id, proveedor, numero, fecha_creacion, monto_total, snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                proyecto_id, proveedor, numero, _ahora(), grupo["subtotal"],
                json.dumps(items_snapshot, ensure_ascii=False),
            ),
        )
        conexion.commit()
    except BaseException:
        conexion.rollback()
        raise
    finally:
        conexion.close()

    return obtener_compras(proyecto_id, propietario_id)


# AUDITORIA_COMPRAS_P0.md, hallazgo P0-2: cantidad/cantidad_comprada son
# columnas REAL (float64) -- restar dos floats acumulados a lo largo de
# varios registros parciales puede dar un resultado que matemáticamente
# "debería" ser exacto (ej. 10.0 - 9.9) pero en binario no lo es
# (0.09999999999999964, no 0.1). Sin tolerancia, una solicitud por
# exactamente lo que falta se rechazaba por "exceder" un pendiente que
# en realidad era el mismo número con ruido de representación. Esta
# tolerancia existe exclusivamente para el ruido binario de float64;
# no representa una tolerancia comercial. Es absoluta y no escala con la
# cantidad: incluso cerca del máximo permitido por el API (1 000 000), un
# exceso real no se vuelve aceptable por ser proporcionalmente pequeño.
TOLERANCIA_CANTIDAD_RELATIVA = 0.0
TOLERANCIA_CANTIDAD_ABSOLUTA = 1e-9


def registrar_compra_item(proyecto_id, propietario_id, item_id, cantidad, monto=None, fecha=None, comprobante_referencia=None):
    """Registra una compra real -- total o parcial -- contra un ítem.

    `cantidad` es lo que se compró EN ESTE registro (no el acumulado
    total) -- se SUMA a cantidad_comprada. Igual `monto`: si se da (el
    monto real pagado, que puede diferir del precio de catálogo por
    fluctuación de precio o descuento real del proveedor), se suma a
    monto_comprado; si no se da, se estima como cantidad × precio y se
    suma igual, para que _calcular_totales() siempre tenga un
    monto_comprado utilizable sin importar si el usuario lo indicó a
    mano.

    Si `cantidad` excede lo que todavía falta por comprar más allá de
    las tolerancias estrictas documentadas arriba, se rechaza el registro completo.
    Nunca se cambia silenciosamente la cantidad que el usuario indicó ni
    se asocia su monto a una cantidad distinta -- la única normalización
    permitida es cuando `cantidad` y `cantidad_pendiente` son
    prácticamente iguales (dentro de esa tolerancia): ahí se usa
    exactamente `cantidad_pendiente` como cantidad efectiva, para que el
    ítem quede en 0 pendiente exacto en vez de arrastrar un residuo de
    punto flotante del tamaño de la tolerancia misma.

    `comprobante_referencia` (ver COMPRAS.md, integración de facturas):
    texto libre para el número de factura/comprobante -- carga manual
    asistida, todavía sin lectura automática de ningún formato."""

    # AUDITORIA_COMPRAS_P0.md, hallazgo P0 (no finitos): NaN e Infinity
    # pasan sin problema los chequeos de rango de arriba -- toda comparación
    # contra NaN da False (así que "cantidad <= 0" nunca lo atrapa), e
    # Infinity SÍ es un REAL válido para SQLite (a diferencia de NaN, que el
    # propio driver de sqlite3 convierte en NULL al bindearlo). Un
    # monto=Infinity committeaba sin error y solo explotaba después, en
    # _calcular_totales()/listar_proyectos() (OverflowError al redondear),
    # ya con el dato corrupto persistido. Se rechaza acá, antes de abrir
    # conexión, para que ningún caller -- HTTP o interno -- dependa
    # únicamente de los límites de Pydantic del router.
    if cantidad is None or not math.isfinite(cantidad) or cantidad <= 0:
        raise ValueError("La cantidad comprada debe ser un número finito mayor a cero")
    if monto is not None and (not math.isfinite(monto) or monto < 0):
        raise ValueError("El monto debe ser un número finito no negativo")

    conexion = conectar()
    try:
        conexion.execute("BEGIN IMMEDIATE")
        item = conexion.execute(
            """
            SELECT i.id, i.cantidad, i.cantidad_comprada, i.monto_comprado, i.precio_al_agregar,
                   pr.precio AS precio_actual
            FROM items_proyecto i
            JOIN proyectos p ON p.id = i.proyecto_id
            LEFT JOIN productos pr ON pr.proveedor = i.proveedor AND pr.id_proveedor = i.id_proveedor
            WHERE i.id = ? AND i.proyecto_id = ? AND p.propietario_id = ?
            """,
            (item_id, proyecto_id, propietario_id),
        ).fetchone()

        if item is None:
            conexion.rollback()
            return None

        precio = item["precio_actual"] if item["precio_actual"] is not None else (item["precio_al_agregar"] or 0)
        cantidad_pendiente = max(item["cantidad"] - (item["cantidad_comprada"] or 0), 0)
        if cantidad_pendiente <= 0:
            raise ValueError("Este ítem ya está completamente comprado")

        cantidades_equivalentes = math.isclose(
            cantidad,
            cantidad_pendiente,
            rel_tol=TOLERANCIA_CANTIDAD_RELATIVA,
            abs_tol=TOLERANCIA_CANTIDAD_ABSOLUTA,
        )
        if cantidades_equivalentes:
            # Prácticamente iguales -- normaliza solo la representación
            # binaria (ver tolerancias declaradas arriba), nunca un
            # exceso real: usa cantidad_pendiente tal cual, no `cantidad`.
            cantidad_efectiva = cantidad_pendiente
        elif cantidad > cantidad_pendiente:
            raise ValueError(
                f"La cantidad solicitada ({cantidad}) excede la cantidad pendiente ({cantidad_pendiente})"
            )
        else:
            cantidad_efectiva = cantidad

        monto_efectivo = monto if monto is not None else round(cantidad_efectiva * precio, 2)
        # SQLite REAL y el contrato del API no declaran una escala máxima.
        # No se redondea prematuramente: cantidades menores a 0.0001 siguen
        # asociadas a su monto. Si la compra cierra el pendiente, fijar el
        # total exacto evita residuos binarios y mantiene estado/cantidad
        # coherentes.
        nueva_cantidad_comprada = (
            item["cantidad"]
            if cantidades_equivalentes
            else (item["cantidad_comprada"] or 0) + cantidad_efectiva
        )
        nuevo_monto_comprado = round((item["monto_comprado"] or 0) + monto_efectivo, 2)
        nuevo_estado = "comprado" if nueva_cantidad_comprada >= item["cantidad"] else "parcial"

        conexion.execute(
            """
            UPDATE items_proyecto
            SET cantidad_comprada = ?, monto_comprado = ?, fecha_compra = ?,
                estado = ?, comprobante_tipo = 'manual',
                comprobante_referencia = COALESCE(?, comprobante_referencia)
            WHERE id = ?
            """,
            (
                nueva_cantidad_comprada, nuevo_monto_comprado, fecha or _ahora(),
                nuevo_estado, comprobante_referencia, item_id,
            ),
        )
        conexion.execute(
            "UPDATE proyectos SET fecha_actualizacion = ? WHERE id = ?", (_ahora(), proyecto_id)
        )
        conexion.commit()
    except BaseException:
        conexion.rollback()
        raise
    finally:
        conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


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

    # Leído ANTES del UPDATE cuando podría ser una aceptación (revisado
    # pasando a True) o un cambio de estado -- es la única forma de saber
    # si esto es una transición real de 0 a 1 (una "aceptación" de
    # verdad, no un PATCH redundante) y, para estado, la cantidad total
    # del ítem (ver Compras más abajo).
    item_previo = None
    if cambios.get("revisado") or "estado" in cambios:
        item_previo = conexion.execute(
            """
            SELECT revisado, origen, confianza_match, proveedor, id_proveedor,
                   categoria_al_agregar, texto_original, fecha_agregado, cantidad
            FROM items_proyecto WHERE id = ? AND proyecto_id = ?
            """,
            (item_id, proyecto_id),
        ).fetchone()

    # Compras (ver COMPRAS.md): el selector rápido de estado sigue
    # funcionando exactamente como antes de Compras -- "comprado" acá
    # significa "se compró todo, sin un monto real registrado todavía"
    # (mismo fallback cantidad×precio que _calcular_totales ya usaba);
    # "pendiente" limpia cualquier rastro de una compra parcial anterior.
    # "parcial" nunca se llega por acá -- solo vía registrar_compra_item,
    # porque sin una cantidad real asociada no significa nada.
    if item_previo is not None and cambios.get("estado") in ("comprado", "pendiente"):
        if cambios["estado"] == "comprado":
            cambios["cantidad_comprada"] = cambios.get("cantidad", item_previo["cantidad"])
            cambios["monto_comprado"] = None
            cambios["fecha_compra"] = _ahora()
        else:
            cambios["cantidad_comprada"] = 0
            cambios["monto_comprado"] = None
            cambios["fecha_compra"] = None

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
    """Reemplaza un ítem por otro elegido a mano en una sola transacción.

    El origen y el destino se validan bajo el mismo write lock antes de
    borrar o insertar. Solo se conserva el evento histórico específico
    ``seleccion_reemplazada``: no se llama a agregar_item ni se genera un
    alta independiente que pueda sugerir que fueron dos decisiones.

    `cantidad`, si no se da, conserva la cantidad del ítem reemplazado."""

    conexion = conectar()
    try:
        conexion.execute("BEGIN IMMEDIATE")
        proyecto = conexion.execute(
            "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
            (proyecto_id, propietario_id),
        ).fetchone()
        if not proyecto:
            conexion.rollback()
            return None

        item_previo = conexion.execute(
            """
            SELECT cantidad, revisado, origen, confianza_match, proveedor, id_proveedor,
                   categoria_al_agregar, texto_original, fecha_agregado,
                   cantidad_comprada, monto_comprado, fecha_compra,
                   comprobante_tipo, comprobante_referencia
            FROM items_proyecto WHERE id = ? AND proyecto_id = ?
            """,
            (item_id, proyecto_id),
        ).fetchone()
        if item_previo is None:
            conexion.rollback()
            return None

        if (
            (item_previo["cantidad_comprada"] or 0) != 0
            or item_previo["monto_comprado"] is not None
            or item_previo["fecha_compra"] is not None
            or item_previo["comprobante_tipo"] is not None
            or item_previo["comprobante_referencia"] is not None
        ):
            raise ValueError("No se puede reemplazar un ítem con compras registradas")

        destino = conexion.execute(
            """
            SELECT id FROM items_proyecto
            WHERE proyecto_id = ? AND proveedor = ? AND id_proveedor = ?
            """,
            (proyecto_id, proveedor_nuevo, id_proveedor_nuevo),
        ).fetchone()
        if destino is not None:
            raise ValueError("El producto de reemplazo ya existe en el proyecto")

        producto = conexion.execute(
            """
            SELECT nombre, marca, categoria, precio, url_imagen, url_producto
            FROM productos WHERE proveedor = ? AND id_proveedor = ?
            """,
            (proveedor_nuevo, id_proveedor_nuevo),
        ).fetchone()
        if producto is None:
            raise ValueError("Producto no encontrado en el catálogo")

        cantidad_final = cantidad if cantidad is not None else item_previo["cantidad"]
        ahora = _ahora()
        cursor = conexion.execute(
            "DELETE FROM items_proyecto WHERE id = ? AND proyecto_id = ?", (item_id, proyecto_id)
        )
        if cursor.rowcount == 0:
            conexion.rollback()
            return None

        conexion.execute(
            """
            INSERT INTO items_proyecto (
                proyecto_id, proveedor, id_proveedor, cantidad,
                nombre_al_agregar, marca_al_agregar, categoria_al_agregar,
                precio_al_agregar, url_imagen_al_agregar, url_producto_al_agregar,
                fecha_agregado, partida, origen, revisado
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', 1)
            """,
            (
                proyecto_id, proveedor_nuevo, id_proveedor_nuevo, cantidad_final,
                producto["nombre"], producto["marca"], producto["categoria"],
                producto["precio"], producto["url_imagen"], producto["url_producto"],
                ahora, _sugerir_partida(producto["categoria"]),
            ),
        )
        conexion.execute(
            "UPDATE proyectos SET fecha_actualizacion = ? WHERE id = ?", (ahora, proyecto_id)
        )

        es_reemplazo_de_sugerencia = (
            not item_previo["revisado"]
            and item_previo["origen"] == "plano"
            and bool(item_previo["confianza_match"])
        )
        _eventos.registrar_evento(
            conexion, _eventos.TIPO_SELECCION_REEMPLAZADA,
            usuario_id=propietario_id, proyecto_id=proyecto_id, item_id=item_id,
            proveedor=proveedor_nuevo, id_proveedor=id_proveedor_nuevo,
            proveedor_anterior=item_previo["proveedor"], id_proveedor_anterior=item_previo["id_proveedor"],
            categoria=item_previo["categoria_al_agregar"], origen=item_previo["origen"],
            confianza_match=item_previo["confianza_match"], texto_material=item_previo["texto_original"],
            tiempo_hasta_decision_segundos=(
                _eventos.segundos_desde(item_previo["fecha_agregado"])
                if es_reemplazo_de_sugerencia else None
            ),
        )
        conexion.commit()
    except BaseException:
        conexion.rollback()
        raise
    finally:
        conexion.close()

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def iniciar_analisis_plano(proyecto_id, propietario_id, ruta_pdf, nombre_archivo, tamano_bytes=None):
    """Mission #002 (Plan Processing Stability): reemplaza el antiguo
    analizar_plano(), que bloqueaba la petición HTTP hasta que el
    análisis terminaba -- verificado directo contra producción, un plano
    de apenas 105MB (35% del límite permitido) ya devuelve 502 a los 43s
    (ver HANDOFF). Ningún timeout interno evita eso: la única forma de
    que la subida nunca falle por esto es que la petición HTTP no espere
    al análisis en absoluto.

    Encola el trabajo pesado (igual que antes, mismo _EXECUTOR_PLANOS,
    mismo _procesar_plano_pdf, sin cambios de comportamiento del lector)
    y devuelve de inmediato -- (proyecto, future). El proyecto ya queda
    con plano_estado='procesando'; quien llama decide qué hacer con el
    future (ver analizar_plano_sincrono(), para clientes que todavía no
    migraron al flujo asíncrono, y api/routers/proyectos.py para el
    flujo asíncrono nuevo).

    `tamano_bytes` es opcional (default None) -- solo el router HTTP lo
    conoce de verdad (lo midió mientras escribía el temporal a disco);
    un caller que llame esta función directo (scripts, pruebas) no tiene
    por qué inventarlo. Se propaga hasta el log estructurado que emite
    _completar_analisis_plano() al terminar (ver
    ANALISIS_INCIDENTE_MEMORIA_RENDER.md) -- nunca se guarda en la base
    ni afecta el resultado del análisis.

    Devuelve None si el proyecto no existe o no es del usuario. Levanta
    AnalisisPlanoEnCurso si el usuario ya tiene otro análisis
    'procesando' -- en cualquier proyecto, no solo este -- para no
    encolar un segundo trabajo silenciosamente (ver Mission #002: límite
    de concurrencia por usuario)."""
    conexion = conectar()
    try:
        conexion.execute("BEGIN IMMEDIATE")
        fila = conexion.execute(
            "SELECT id FROM proyectos WHERE id = ? AND propietario_id = ?",
            (proyecto_id, propietario_id),
        ).fetchone()
        if not fila:
            conexion.rollback()
            return None

        en_curso = conexion.execute(
            "SELECT COUNT(*) AS total FROM proyectos WHERE propietario_id = ? AND plano_estado = 'procesando'",
            (propietario_id,),
        ).fetchone()
        if en_curso["total"] > 0:
            conexion.rollback()
            raise AnalisisPlanoEnCurso()

        token = secrets.token_urlsafe(16)
        conexion.execute(
            """
            UPDATE proyectos
            SET plano_estado = 'procesando', plano_procesamiento_id = ?, plano_error_mensaje = NULL
            WHERE id = ?
            """,
            (token, proyecto_id),
        )
        conexion.commit()
    finally:
        conexion.close()

    _logger.info(
        f"ANALISIS_PLANO id={_id_peticion()} proyecto_id={proyecto_id} estado=encolado "
        f"token={token} tamano_bytes={tamano_bytes}"
    )

    # ANALISIS_INCIDENTE_MEMORIA_RENDER.md: el momento en que se encola es
    # el punto de referencia real para medir cuánto tarda un análisis --
    # capturado acá, no dentro del callback, porque el callback no tiene
    # forma propia de saber cuándo arrancó este intento en particular.
    inicio = time.perf_counter()

    future = _EXECUTOR_PLANOS.submit(_procesar_plano_pdf, ruta_pdf)
    # Armar el watchdog ANTES de enganchar el callback -- si el future ya
    # está resuelto en el momento de add_done_callback() (nunca pasa en
    # producción, donde el trabajo real siempre tarda; sí puede pasar con
    # un future ya resuelto en una prueba), el callback corre SINCRÓNICO
    # dentro de add_done_callback() y llama _cancelar_watchdog() de
    # inmediato -- con este orden, siempre encuentra el timer ya armado
    # para cancelar, en vez de dejarlo huérfano corriendo 120s de más.
    _armar_watchdog(proyecto_id, token)
    future.add_done_callback(
        functools.partial(_completar_analisis_plano, proyecto_id, token, nombre_archivo, tamano_bytes, inicio)
    )

    return obtener_proyecto(proyecto_id, propietario_id=propietario_id), future


def _completar_analisis_plano(proyecto_id, token, nombre_archivo, tamano_bytes, inicio, future):
    """Callback de future.add_done_callback() -- corre en el hilo
    administrador interno de _EXECUTOR_PLANOS, NUNCA en un hilo de
    petición HTTP (ver Mission #002). Es la única función que persiste el
    resultado final (éxito o error) -- tanto el flujo asíncrono como
    analizar_plano_sincrono() (clientes viejos) dependen de que ESTA
    función, y solo ella, escriba el resultado, para no tener dos rutas
    de guardado que puedan desincronizarse.

    El filtro `AND plano_procesamiento_id = ?` en ambos UPDATE evita que
    el resultado de un intento viejo pise el estado de uno más nuevo (ej.
    el usuario borró el plano y subió otro mientras este todavía
    procesaba) -- si el proyecto ya tiene un token distinto, rowcount
    queda en 0 y este resultado simplemente se descarta.

    `tamano_bytes`/`duracion_ms`/`resultado` en el log estructurado (ver
    ANALISIS_INCIDENTE_MEMORIA_RENDER.md): antes de esto, un plano que
    hacía fallar el análisis no dejaba ningún rastro propio más allá del
    estado 'error' en la base -- nada decía cuánto había tardado en
    fallar, de qué tamaño era el archivo, ni qué tipo de excepción fue,
    exactamente el dato que hace falta para diagnosticar un incidente
    real (ej. de memoria) sin poder reproducirlo a mano."""
    _cancelar_watchdog(token)

    conexion = conectar()
    try:
        try:
            analisis = future.result()
        except Exception as error:
            duracion_ms = round((time.perf_counter() - inicio) * 1000, 1)
            _logger.exception(
                f"ANALISIS_PLANO fallo proyecto_id={proyecto_id} token={token} "
                f"tamano_bytes={tamano_bytes} duracion_ms={duracion_ms} "
                f"resultado=fallo error={type(error).__name__}"
            )
            conexion.execute(
                """
                UPDATE proyectos SET plano_estado = 'error', plano_error_mensaje = ?
                WHERE id = ? AND plano_procesamiento_id = ?
                """,
                (MENSAJE_ERROR_GENERICO, proyecto_id, token),
            )
            conexion.commit()
            return

        duracion_ms = round((time.perf_counter() - inicio) * 1000, 1)
        ahora = _ahora()
        conexion.execute(
            """
            UPDATE proyectos
            SET plano_nombre_archivo = ?, plano_analisis = ?, plano_fecha_analisis = ?,
                plano_estado = 'listo', plano_error_mensaje = NULL, fecha_actualizacion = ?
            WHERE id = ? AND plano_procesamiento_id = ?
            """,
            (nombre_archivo, json.dumps(analisis), ahora, ahora, proyecto_id, token),
        )
        conexion.commit()
        _logger.info(
            f"ANALISIS_PLANO completado proyecto_id={proyecto_id} token={token} "
            f"tamano_bytes={tamano_bytes} duracion_ms={duracion_ms} resultado=exito "
            f"laminas={analisis.get('cantidad_laminas')}"
        )
    finally:
        conexion.close()


def _manejar_timeout_analisis(proyecto_id, token):
    """Dispara TIMEOUT_ANALISIS_SEGUNDOS después de encolar (ver
    _armar_watchdog) si _completar_analisis_plano() no canceló este timer
    antes -- o sea, el análisis sigue corriendo mucho más de lo normal.

    El filtro `AND plano_estado = 'procesando'` evita pisar un resultado
    que haya llegado justo antes de que este timer disparara (carrera
    inofensiva: si ya está 'listo' o 'error', rowcount queda en 0 y no se
    hace nada más). Si SÍ éramos los primeros en marcarlo -- rowcount==1,
    de verdad estaba colgado -- se intenta liberar el worker atascado
    (ver _reciclar_executor_planos, best-effort, no garantizado)."""
    conexion = conectar()
    try:
        cursor = conexion.execute(
            """
            UPDATE proyectos SET plano_estado = 'error', plano_error_mensaje = ?
            WHERE id = ? AND plano_procesamiento_id = ? AND plano_estado = 'procesando'
            """,
            (MENSAJE_ERROR_TIMEOUT, proyecto_id, token),
        )
        conexion.commit()
        genuinamente_colgado = cursor.rowcount == 1
    finally:
        conexion.close()

    with _LOCK_TEMPORIZADORES:
        _TEMPORIZADORES_ANALISIS.pop(token, None)

    if genuinamente_colgado:
        _logger.warning(f"ANALISIS_PLANO timeout proyecto_id={proyecto_id} token={token}")
        _reciclar_executor_planos()


def analizar_plano_sincrono(proyecto_id, propietario_id, ruta_pdf, nombre_archivo, tamano_bytes=None):
    """Compatibilidad para clientes que todavía no migraron al flujo
    asíncrono de Mission #002 (ver api/routers/proyectos.py: parámetro
    `asincronico`) -- mismo comportamiento observable que el antiguo
    analizar_plano(): bloquea hasta que el análisis termina y devuelve el
    proyecto completo. Reutiliza el mismo mecanismo interno
    (iniciar_analisis_plano + el mismo callback que persiste el
    resultado) que el flujo asíncrono -- no hay una segunda
    implementación del análisis, solo una segunda forma de esperar su
    resultado. Diferencia real con el comportamiento de antes: ahora
    tiene un techo real (TIMEOUT_ANALISIS_SEGUNDOS) en vez de esperar sin
    límite.

    `tamano_bytes` (ver ANALISIS_INCIDENTE_MEMORIA_RENDER.md): se
    propaga tal cual a iniciar_analisis_plano(), mismo criterio ahí."""
    resultado = iniciar_analisis_plano(
        proyecto_id, propietario_id, ruta_pdf, nombre_archivo, tamano_bytes=tamano_bytes
    )
    if resultado is None:
        return None

    proyecto, future = resultado

    # Levanta concurrent.futures.TimeoutError o la excepción real del
    # análisis -- en ambos casos, el router ya mapea cualquier excepción
    # de esta función a un 422 genérico, mismo contrato que antes de esta
    # misión. El estado 'error' correspondiente ya lo escribe
    # _completar_analisis_plano() o _manejar_timeout_analisis(), no acá.
    future.result(timeout=TIMEOUT_ANALISIS_SEGUNDOS)

    return _esperar_persistencia_callback(proyecto_id, propietario_id)


def _esperar_persistencia_callback(proyecto_id, propietario_id, intentos=40, intervalo_segundos=0.05):
    """future.result() ya haber retornado NO garantiza que
    _completar_analisis_plano() ya haya terminado de escribir en la base
    -- concurrent.futures.Future notifica a quien espera en .result()
    ANTES de invocar los done-callbacks (verificado en el código fuente
    de concurrent.futures.Future.set_result), así que hay una ventana
    real, aunque angosta, donde este hilo podría despertar antes de que
    el callback haya escrito el resultado. Una espera corta y acotada
    (máximo ~2s) cierra esa ventana sin necesitar una segunda ruta de
    escritura -- ver _completar_analisis_plano, que sigue siendo la única
    función que persiste el resultado."""
    for _ in range(intentos):
        proyecto = obtener_proyecto(proyecto_id, propietario_id=propietario_id)
        if proyecto is None or proyecto.get("plano_estado") != "procesando":
            return proyecto
        time.sleep(intervalo_segundos)

    # No debería pasar nunca en la práctica -- el future ya terminó, el
    # callback ya se disparó. Si por lo que sea la escritura no llegó a
    # tiempo, devolver el estado actual (probablemente todavía
    # 'procesando') es la opción segura -- nunca colgar el request.
    return obtener_proyecto(proyecto_id, propietario_id=propietario_id)


def recuperar_analisis_interrumpidos():
    """Corre una sola vez al arrancar (ver api/main.py, antes de aceptar
    tráfico real de planos), después de que las migraciones terminen.
    Cualquier proyecto en plano_estado='procesando' en este punto es, por
    definición, huérfano: el Future y su callback vivían en el proceso
    anterior y no sobreviven un reinicio o redeploy -- sin este sweep, el
    cliente quedaría consultando el estado para siempre sin que nadie lo
    actualice nunca (ver Mission #002: recuperación ante restart)."""
    conexion = conectar()
    try:
        cursor = conexion.execute(
            "UPDATE proyectos SET plano_estado = 'error', plano_error_mensaje = ? WHERE plano_estado = 'procesando'",
            (MENSAJE_ERROR_INTERRUPCION,),
        )
        conexion.commit()
        afectados = cursor.rowcount
    finally:
        conexion.close()

    if afectados:
        _logger.warning(f"ANALISIS_PLANO recuperados={afectados} causa=reinicio_de_proceso")
    return afectados


def eliminar_plano(proyecto_id, propietario_id):
    """Corrección de revisión (Mission #002, hallazgo P1 de Emma): antes,
    esto limpiaba plano_nombre_archivo/plano_analisis/plano_fecha_analisis
    pero dejaba plano_estado/plano_procesamiento_id intactos -- si el
    usuario borraba un plano mientras seguía 'procesando' en segundo
    plano, dos cosas fallaban: (1) el callback tardío de ESE análisis
    (ver _completar_analisis_plano) todavía encontraba el mismo token, así
    que su UPDATE guardado por token SÍ pisaba, resucitando el plano recién
    borrado con el resultado viejo; (2) el usuario quedaba bloqueado por
    _reclamar el slot de análisis (ver iniciar_analisis_plano) hasta que
    ese análisis huérfano terminara o el watchdog lo diera por vencido a
    los TIMEOUT_ANALISIS_SEGUNDOS, aunque ya no le quedara ningún plano
    que mostrar como resultado.

    Limpiar plano_procesamiento_id acá invalida el token de inmediato --
    cualquier callback o timeout tardío para ESE token deja de encontrar
    coincidencia (WHERE ... AND plano_procesamiento_id = ?), así que su
    UPDATE se vuelve no-op, sin necesidad de coordinarse con el hilo que
    lo esté corriendo. Limpiar plano_estado a NULL (no 'error' ni ningún
    otro valor) es el estado correcto: "nunca se subió un plano" es
    exactamente lo que queda después de este borrado, y además libera al
    usuario del reclamo de concurrencia en el mismo instante -- puede
    iniciar un análisis nuevo de inmediato, sin esperar nada."""
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
        SET plano_nombre_archivo = NULL, plano_analisis = NULL, plano_fecha_analisis = NULL,
            plano_estado = NULL, plano_procesamiento_id = NULL, plano_error_mensaje = NULL,
            fecha_actualizacion = ?
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
    inicio = time.perf_counter()

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

    duracion_ms = round((time.perf_counter() - inicio) * 1000, 1)
    _logger.info(
        f"COTIZACION_AUTOMATICA id={_id_peticion()} proyecto_id={proyecto_id} "
        f"duracion_ms={duracion_ms} agregados={resumen['agregados']} sin_seleccion={resumen['sin_seleccion']}"
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
