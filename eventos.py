"""Instrumentación del ciclo de vida de una selección de producto --
sugerida por el motor automático (seleccion_automatica.py) o agregada a
mano -- para que Proyecta empiece a acumular datos reales de decisiones
de usuarios (ver ARQUITECTURA_RECOMENDACION_V2.md, Fase 0). Ningún
componente de este módulo usa IA ni entrena nada todavía -- es
exclusivamente instrumentación (registrar) y agregación (contar/medir lo
ya registrado), la base de la que dependen las fases futuras del
documento de arquitectura.

`registrar_evento()` es la única forma de escribir en la tabla `eventos`
(ver database/agregar_eventos.py) -- se llama desde
api/repositorio_proyectos.py, en los mismos puntos donde ya se
modificaba items_proyecto (agregar_item/actualizar_item/eliminar_item/
reemplazar_item), usando la MISMA conexión/transacción que esa
modificación, nunca una aparte -- así un evento nunca queda huérfano
(escrito sin que el cambio real haya ocurrido) ni al revés.

Cinco tipos de evento cubren todo el ciclo de vida de una selección:

- item_agregado: cualquier producto agregado a un proyecto, sin importar
  el origen (plano/manual/sistema_constructivo/plantilla). Cuando
  origen='plano' y confianza_match no es NULL, ESTE evento ES la
  "sugerencia automática" -- no hay un tipo separado para eso, se filtra
  por esas dos columnas (ver `_ES_SUGERENCIA_AUTOMATICA` abajo).
- seleccion_aceptada: un ítem sugerido por el motor pasó de
  revisado=0 a revisado=1 sin reemplazo (el usuario lo aprobó tal cual).
- seleccion_reemplazada: un ítem sugerido por el motor se cambió por
  otro elegido a mano -- lleva tanto el producto ANTERIOR (columnas
  *_anterior) como el NUEVO (columnas proveedor/id_proveedor), algo que
  dos eventos separados (uno de baja, uno de alta) no podrían
  reconstruir de forma confiable si hay varias decisiones pendientes al
  mismo tiempo.
- seleccion_eliminada: un ítem sugerido por el motor se descartó sin
  reemplazo, mientras seguía pendiente de revisión.
- item_eliminado: cualquier otro borrado (un ítem ya revisado, o de
  origen manual) -- se distingue de seleccion_eliminada porque no es una
  señal sobre la CALIDAD de una sugerencia automática, solo un borrado
  común."""

import json
from datetime import datetime, timedelta

from db import conectar

TIPO_ITEM_AGREGADO = "item_agregado"
TIPO_SELECCION_ACEPTADA = "seleccion_aceptada"
TIPO_SELECCION_REEMPLAZADA = "seleccion_reemplazada"
TIPO_SELECCION_ELIMINADA = "seleccion_eliminada"
TIPO_ITEM_ELIMINADO = "item_eliminado"

TIPOS_EVENTO = {
    TIPO_ITEM_AGREGADO,
    TIPO_SELECCION_ACEPTADA,
    TIPO_SELECCION_REEMPLAZADA,
    TIPO_SELECCION_ELIMINADA,
    TIPO_ITEM_ELIMINADO,
}

NIVELES_CONFIANZA = ("alta", "media", "baja")

_FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

# Una fila de eventos (de cualquiera de los 4 tipos ligados al ciclo de
# vida de una sugerencia) es "sobre una sugerencia automática" cuando
# reúne estas dos condiciones -- el mismo filtro se repite en cada
# función de dashboard de este módulo, centralizado acá para no
# desincronizarse.
_FILTRO_SUGERENCIA_AUTOMATICA = "origen = 'plano' AND confianza_match IS NOT NULL"


def _ahora():
    return datetime.now().strftime(_FORMATO_FECHA)


def segundos_desde(fecha_texto):
    """Segundos transcurridos entre `fecha_texto` (mismo formato que
    _ahora()) y ahora -- usado para tiempo_hasta_decision_segundos. None
    si fecha_texto es None/no parseable, nunca lanza."""
    if not fecha_texto:
        return None
    try:
        momento = datetime.strptime(fecha_texto, _FORMATO_FECHA)
    except ValueError:
        return None
    return (datetime.now() - momento).total_seconds()


def registrar_evento(
    conexion,
    tipo,
    *,
    usuario_id=None,
    proyecto_id=None,
    item_id=None,
    proveedor=None,
    id_proveedor=None,
    proveedor_anterior=None,
    id_proveedor_anterior=None,
    categoria=None,
    origen=None,
    confianza_match=None,
    texto_material=None,
    tiempo_hasta_decision_segundos=None,
    datos_extra=None,
):
    """Inserta una fila en `eventos` usando la conexión ya abierta por el
    caller -- a propósito no abre ni cierra su propia conexión, para que
    el INSERT participe de la misma transacción que el cambio real que
    lo origina (ver el docstring del módulo)."""

    if tipo not in TIPOS_EVENTO:
        raise ValueError(f"Tipo de evento inválido: {tipo}")

    conexion.execute(
        """
        INSERT INTO eventos (
            tipo, usuario_id, proyecto_id, item_id,
            proveedor, id_proveedor, proveedor_anterior, id_proveedor_anterior,
            categoria, origen, confianza_match, texto_material,
            tiempo_hasta_decision_segundos, datos_extra, fecha_creacion
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tipo, usuario_id, proyecto_id, item_id,
            proveedor, id_proveedor, proveedor_anterior, id_proveedor_anterior,
            categoria, origen, confianza_match, texto_material,
            tiempo_hasta_decision_segundos,
            json.dumps(datos_extra) if datos_extra is not None else None,
            _ahora(),
        ),
    )


def _filtro_fecha(dias):
    if dias is None:
        return "", []
    limite = (datetime.now() - timedelta(days=dias)).strftime(_FORMATO_FECHA)
    return "AND fecha_creacion >= ?", [limite]


def resumen_seleccion_automatica(dias=None):
    """Precisión y tasa de aceptación del motor de selección automática
    -- global y por nivel de confianza (alta/media/baja). Distingue
    deliberadamente dos métricas (ver ARQUITECTURA_RECOMENDACION_V2.md):

    - tasa_aceptacion = aceptadas / sugeridas -- sobre TODAS las
      sugerencias, incluidas las que un usuario todavía no revisó.
    - precision = aceptadas / (aceptadas + reemplazadas + eliminadas) --
      solo sobre las que YA se decidieron, sin castigar al motor por
      decisiones pendientes que ni siquiera se han evaluado todavía.

    `dias`, si se da, acota a eventos de los últimos N días (None = todo
    el historial)."""

    conexion = conectar()
    filtro, params = _filtro_fecha(dias)

    filas_sugeridas = conexion.execute(
        f"""
        SELECT confianza_match, COUNT(*) AS n
        FROM eventos
        WHERE tipo = ? AND {_FILTRO_SUGERENCIA_AUTOMATICA} {filtro}
        GROUP BY confianza_match
        """,
        (TIPO_ITEM_AGREGADO, *params),
    ).fetchall()

    filas_decisiones = conexion.execute(
        f"""
        SELECT tipo, confianza_match, COUNT(*) AS n,
               AVG(tiempo_hasta_decision_segundos) AS tiempo_promedio_segundos
        FROM eventos
        WHERE tipo IN (?, ?, ?) {filtro}
        GROUP BY tipo, confianza_match
        """,
        (TIPO_SELECCION_ACEPTADA, TIPO_SELECCION_REEMPLAZADA, TIPO_SELECCION_ELIMINADA, *params),
    ).fetchall()
    conexion.close()

    sugeridas_por_confianza = {fila["confianza_match"]: fila["n"] for fila in filas_sugeridas}

    por_confianza = {}
    for nivel in NIVELES_CONFIANZA:
        aceptadas = reemplazadas = eliminadas = 0
        tiempo_promedio_aceptar = None
        for fila in filas_decisiones:
            if fila["confianza_match"] != nivel:
                continue
            if fila["tipo"] == TIPO_SELECCION_ACEPTADA:
                aceptadas = fila["n"]
                tiempo_promedio_aceptar = fila["tiempo_promedio_segundos"]
            elif fila["tipo"] == TIPO_SELECCION_REEMPLAZADA:
                reemplazadas = fila["n"]
            elif fila["tipo"] == TIPO_SELECCION_ELIMINADA:
                eliminadas = fila["n"]

        sugeridas = sugeridas_por_confianza.get(nivel, 0)
        decididas = aceptadas + reemplazadas + eliminadas
        por_confianza[nivel] = {
            "sugeridas": sugeridas,
            "aceptadas": aceptadas,
            "reemplazadas": reemplazadas,
            "eliminadas": eliminadas,
            "pendientes": max(sugeridas - decididas, 0),
            "tasa_aceptacion": round(aceptadas / sugeridas, 4) if sugeridas else None,
            "precision": round(aceptadas / decididas, 4) if decididas else None,
            "tiempo_promedio_aceptar_segundos": (
                round(tiempo_promedio_aceptar, 1) if tiempo_promedio_aceptar is not None else None
            ),
        }

    total_sugeridas = sum(v["sugeridas"] for v in por_confianza.values())
    total_aceptadas = sum(v["aceptadas"] for v in por_confianza.values())
    total_reemplazadas = sum(v["reemplazadas"] for v in por_confianza.values())
    total_eliminadas = sum(v["eliminadas"] for v in por_confianza.values())
    total_decididas = total_aceptadas + total_reemplazadas + total_eliminadas

    return {
        "por_confianza": por_confianza,
        "total": {
            "sugeridas": total_sugeridas,
            "aceptadas": total_aceptadas,
            "reemplazadas": total_reemplazadas,
            "eliminadas": total_eliminadas,
            "pendientes": max(total_sugeridas - total_decididas, 0),
            "tasa_aceptacion": round(total_aceptadas / total_sugeridas, 4) if total_sugeridas else None,
            "precision": round(total_aceptadas / total_decididas, 4) if total_decididas else None,
        },
    }


def _desempeno_agrupado(columna, limite, dias, minimo_sugerencias):
    """Compartido por materiales_mas_dificiles/categorias_peor_desempeno
    -- misma consulta, agrupada por una columna distinta. `columna` nunca
    viene de afuera (siempre uno de los dos valores fijos de abajo), así
    que interpolarla en el SQL es seguro -- no es entrada de usuario."""

    assert columna in ("texto_material", "categoria")

    conexion = conectar()
    filtro, params = _filtro_fecha(dias)

    filas = conexion.execute(
        f"""
        SELECT
            {columna} AS clave,
            SUM(CASE WHEN tipo = ? THEN 1 ELSE 0 END) AS sugeridas,
            SUM(CASE WHEN tipo = ? THEN 1 ELSE 0 END) AS aceptadas,
            SUM(CASE WHEN tipo = ? THEN 1 ELSE 0 END) AS reemplazadas,
            SUM(CASE WHEN tipo = ? THEN 1 ELSE 0 END) AS eliminadas
        FROM eventos
        WHERE {columna} IS NOT NULL AND {_FILTRO_SUGERENCIA_AUTOMATICA}
          AND tipo IN (?, ?, ?, ?)
          {filtro}
        GROUP BY {columna}
        """,
        (
            TIPO_ITEM_AGREGADO, TIPO_SELECCION_ACEPTADA, TIPO_SELECCION_REEMPLAZADA, TIPO_SELECCION_ELIMINADA,
            TIPO_ITEM_AGREGADO, TIPO_SELECCION_ACEPTADA, TIPO_SELECCION_REEMPLAZADA, TIPO_SELECCION_ELIMINADA,
            *params,
        ),
    ).fetchall()
    conexion.close()

    resultado = []
    for fila in filas:
        sugeridas = fila["sugeridas"]
        if sugeridas < minimo_sugerencias:
            continue
        rechazadas = fila["reemplazadas"] + fila["eliminadas"]
        resultado.append({
            columna: fila["clave"],
            "sugeridas": sugeridas,
            "aceptadas": fila["aceptadas"],
            "reemplazadas": fila["reemplazadas"],
            "eliminadas": fila["eliminadas"],
            "tasa_rechazo": round(rechazadas / sugeridas, 4),
        })

    resultado.sort(key=lambda fila: (fila["tasa_rechazo"], fila["sugeridas"]), reverse=True)
    return resultado[:limite]


def materiales_mas_dificiles(limite=20, dias=None, minimo_sugerencias=2):
    """Materiales (texto_material, el texto crudo del material tal como
    aparecía en el plano) con mayor tasa de reemplazo/eliminación --
    dónde el motor automático se equivoca más seguido. `minimo_sugerencias`
    descarta materiales con muy pocas observaciones (una sola sugerencia
    rechazada da 100% de rechazo, pero no dice nada confiable todavía)."""

    return _desempeno_agrupado("texto_material", limite, dias, minimo_sugerencias)


def categorias_peor_desempeno(limite=20, dias=None, minimo_sugerencias=3):
    """Mismo cálculo que materiales_mas_dificiles, agrupado por categoría
    del producto en vez de por material -- para ver si el problema es
    sistemático de una categoría entera (ej. "Vidrios") en vez de un
    material puntual."""

    return _desempeno_agrupado("categoria", limite, dias, minimo_sugerencias)
