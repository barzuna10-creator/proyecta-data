"""Runner de migraciones para producción.

Causa raíz que esto resuelve: `POST /auth/registro` fallaba en Render con
`sqlite3.OperationalError: no such table: usuarios`. El código nuevo se
desplegó, pero ningún paso del despliegue -- ni `buildCommand` (Render no
monta el disco persistente durante el build), ni `startCommand`, ni el
propio arranque de la app -- ejecutaba `database/agregar_autenticacion.py`
contra la base real. Todas las migraciones de este proyecto (los 8
scripts `database/agregar_*.py`) se corrieron siempre a mano, en local.
Este módulo cierra ese hueco de forma genérica, para esta y cualquier
migración futura.

Diseño, punto por punto:

- Registro EXPLÍCITO (`MIGRACIONES` abajo) -- nunca se descubren archivos
  por convención de nombre. Agregar una migración nueva requiere agregar
  una línea acá a propósito.
- Tabla `migraciones_aplicadas(nombre PRIMARY KEY)` exclusivamente como
  registro de finalización. Nunca funciona como lock ni se escribe antes
  de que el cuerpo haya retornado después de su commit.
- Lock de archivo separado para serializar procesos. Un reinicio que llega
  mientras otro proceso migra espera el lock y vuelve a verificar la base.
- Verificación real de tablas, columnas, índices y conversiones. Registro y
  esquema deben coincidir; la única adopción permitida es el baseline legacy
  explícito, validado completo antes de crear sus marcadores.
- Fallo estricto: una migración fallida detiene el runner. La API ejecuta el
  runner antes del `yield` de su lifespan, por lo que nunca alcanza readiness
  con un esquema incompleto.
- Reutiliza el `main()` de cada `agregar_*.py` tal cual -- este módulo no
  reescribe ni un renglón de la lógica de ninguna migración existente.

Verificado en vivo, no solo con pruebas unitarias con dobles de prueba
(`tests/test_migraciones.py`): (1) contra un archivo vacío real, que crea
productos antes de ejecutar el resto; (2) contra el snapshot histórico
`48a3fcb` sin registro -- adoptó únicamente las firmas completas, conservó
60,421 productos y 16 proyectos y aplicó las migraciones restantes; (3) una
segunda ejecución saltó las 15 migraciones con el esquema verificado.

Costo conocido: una base realmente nueva ejecuta todas las migraciones,
incluidas las que reconstruyen búsqueda y equivalencias. Ese primer arranque
puede tardar varios minutos con un catálogo poblado. El lifespan espera a que
termine: la latencia de arranque es preferible a servir contra un esquema que
todavía no fue verificado.
"""

import fcntl
import os
import time
from contextlib import contextmanager

import db
from db import conectar
from api.observabilidad import _asegurar_handler, logger

import database.crear_base as _m_esquema_fundacional
import database.agregar_familias_producto as _m_familias_producto
import database.agregar_indice_busqueda as _m_indice_busqueda
import database.agregar_proyectos as _m_proyectos
import database.agregar_cotizaciones as _m_cotizaciones
import database.agregar_equivalencias as _m_equivalencias
import database.agregar_plano_proyecto as _m_plano_proyecto
import database.agregar_trazabilidad_items as _m_trazabilidad_items
import database.agregar_autenticacion as _m_autenticacion
import database.agregar_seleccion_automatica as _m_seleccion_automatica
import database.agregar_eventos as _m_eventos
import database.agregar_indice_url_producto as _m_indice_url_producto
import database.agregar_control_costos as _m_control_costos
import database.agregar_compras as _m_compras
import database.agregar_calculo_compra as _m_calculo_compra

# El esquema fundacional va primero: todas las bases nuevas necesitan la tabla
# productos antes de que las migraciones históricas intenten alterarla.
# Autenticación sigue inmediatamente después porque no depende del catálogo ni
# de proyectos y fue el origen del runner de producción.
#
# El resto conserva su orden histórico real de introducción (ver git log):
# importa porque una migración posterior altera una tabla que una anterior
# crea (ej. agregar_cotizaciones.py hace ALTER TABLE sobre proyectos e
# items_proyecto, creadas por agregar_proyectos.py).
MIGRACIONES = [
    ("crear_esquema_fundacional", _m_esquema_fundacional.main),
    ("agregar_autenticacion", _m_autenticacion.main),
    ("agregar_familias_producto", _m_familias_producto.main),
    ("agregar_indice_busqueda", _m_indice_busqueda.main),
    ("agregar_proyectos", _m_proyectos.main),
    ("agregar_cotizaciones", _m_cotizaciones.main),
    ("agregar_equivalencias", _m_equivalencias.main),
    ("agregar_plano_proyecto", _m_plano_proyecto.main),
    ("agregar_trazabilidad_items", _m_trazabilidad_items.main),
    # Depende de items_proyecto (agregar_proyectos) -- va al final, mismo
    # criterio que el resto de las ALTER TABLE sin urgencia de arranque.
    ("agregar_seleccion_automatica", _m_seleccion_automatica.main),
    # Tabla nueva, sin llaves foráneas (ver database/agregar_eventos.py) --
    # no depende de ninguna de las anteriores, va al final solo por orden
    # histórico de introducción, igual que el resto.
    ("agregar_eventos", _m_eventos.main),
    # Índice sobre una columna que ya existe en productos -- no depende de
    # ninguna de las anteriores (ver database/agregar_indice_url_producto.py,
    # RELEASE_CANDIDATE.md).
    ("agregar_indice_url_producto", _m_indice_url_producto.main),
    # Tabla nueva, sin llave foránea (ver database/agregar_control_costos.py)
    # -- no depende de ninguna de las anteriores, va al final por el mismo
    # criterio que agregar_eventos.
    ("agregar_control_costos", _m_control_costos.main),
    # Depende de items_proyecto (agregar_proyectos) -- va al final por el
    # mismo criterio que el resto de las ALTER TABLE sin urgencia de
    # arranque (ver database/agregar_compras.py).
    ("agregar_compras", _m_compras.main),
    # P0-01: depende de proyectos, items_proyecto, presupuesto_congelado y
    # compras. Solo agrega columnas y la tabla central de conversiones.
    ("agregar_calculo_compra", _m_calculo_compra.main),
]


class ErrorEsquema(RuntimeError):
    """El registro de migraciones y el esquema real no son confiables."""


# Firma mínima observable de cada migración registrada. El registro nunca es
# suficiente por sí solo: cada arranque contrasta estas tablas, columnas e
# índices contra sqlite_master/PRAGMA antes de aceptar tráfico.
REQUISITOS_ESQUEMA = {
    "crear_esquema_fundacional": {
        "tablas": {"productos"},
        "columnas": {"productos": _m_esquema_fundacional.COLUMNAS_PRODUCTOS},
    },
    "agregar_autenticacion": {
        "tablas": {"usuarios", "sesiones", "feedback"},
        "indices": {"idx_sesiones_usuario"},
    },
    "agregar_familias_producto": {
        "tablas": {"familias_producto"},
        "columnas": {"productos": {"familia_id"}},
    },
    "agregar_indice_busqueda": {"tablas": {"productos_fts"}},
    "agregar_proyectos": {
        "tablas": {"proyectos", "items_proyecto"},
        "indices": {"idx_proyectos_propietario", "idx_items_proyecto_proyecto"},
    },
    "agregar_cotizaciones": {
        "columnas": {
            "proyectos": {
                "cliente", "direccion", "area_m2", "indirectos_porcentaje",
                "imprevistos_porcentaje", "margen_porcentaje",
            },
            "items_proyecto": {"partida"},
        },
    },
    "agregar_equivalencias": {
        "tablas": {"grupos_equivalencia"},
        "columnas": {"productos": {"equivalencia_id"}},
        "indices": {"idx_producto_equivalencia"},
    },
    "agregar_plano_proyecto": {
        "columnas": {
            "proyectos": {"plano_nombre_archivo", "plano_analisis", "plano_fecha_analisis"},
        },
    },
    "agregar_trazabilidad_items": {
        "columnas": {
            "items_proyecto": {
                "origen", "pagina_fuente", "lamina_fuente", "texto_original",
                "confianza", "regla_generadora",
            },
        },
    },
    "agregar_seleccion_automatica": {
        "columnas": {"items_proyecto": {"confianza_match", "revisado"}},
    },
    "agregar_eventos": {
        "tablas": {"eventos"},
        "indices": {
            "idx_eventos_tipo", "idx_eventos_proyecto", "idx_eventos_fecha",
            "idx_eventos_categoria", "idx_eventos_texto_material",
        },
    },
    "agregar_indice_url_producto": {"indices": {"idx_producto_url"}},
    "agregar_control_costos": {
        "tablas": {"presupuesto_congelado"},
        "indices": {"idx_presupuesto_congelado_proyecto"},
    },
    "agregar_compras": {
        "tablas": {"ordenes_compra"},
        "columnas": {
            "items_proyecto": {
                "cantidad_comprada", "monto_comprado", "fecha_compra",
                "comprobante_tipo", "comprobante_referencia",
            },
        },
        "indices": {"idx_ordenes_compra_proyecto"},
    },
    "agregar_calculo_compra": {
        "tablas": {"conversiones_unidad"},
        "columnas": {
            "proyectos": {nombre for nombre, _ in _m_calculo_compra.COLUMNAS_PROYECTOS},
            "items_proyecto": {nombre for nombre, _ in _m_calculo_compra.COLUMNAS_ITEMS},
            "presupuesto_congelado": {
                nombre for nombre, _ in _m_calculo_compra.COLUMNAS_PRESUPUESTO
            },
        },
    },
}

# Snapshot histórico conocido anterior al registro de migraciones. Estas
# firmas deben estar completas para adoptar una base legacy; una base más
# antigua o parcialmente creada se rechaza en lugar de adivinar su estado.
BASELINE_LEGACY_REQUERIDA = {
    "crear_esquema_fundacional",
    "agregar_familias_producto",
    "agregar_indice_busqueda",
    "agregar_proyectos",
    "agregar_cotizaciones",
    "agregar_equivalencias",
    "agregar_plano_proyecto",
    "agregar_trazabilidad_items",
}


def _asegurar_tabla_seguimiento(conexion):
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS migraciones_aplicadas (
            nombre TEXT PRIMARY KEY,
            fecha_aplicada TEXT NOT NULL
        )
        """
    )
    conexion.commit()


def _esta_aplicada(conexion, nombre):
    return conexion.execute(
        "SELECT 1 FROM migraciones_aplicadas WHERE nombre = ?", (nombre,)
    ).fetchone() is not None


def _marcar_aplicada(conexion, nombre):
    """Registra éxito únicamente después de que el cuerpo retornó.

    Cada migración hace commit antes de retornar. Esta escritura ocurre en
    una transacción posterior e independiente; nunca funciona como reclamo.
    """
    conexion.execute(
        "INSERT INTO migraciones_aplicadas (nombre, fecha_aplicada) VALUES (?, ?)",
        (nombre, time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conexion.commit()


def _ruta_base_real():
    conexion = conectar()
    try:
        fila = conexion.execute("PRAGMA database_list").fetchone()
        ruta = fila[2] if fila else ""
    finally:
        conexion.close()
    return os.path.abspath(ruta or db.BASE_DATOS)


@contextmanager
def _bloqueo_exclusivo_runner():
    """Serializa runners sin usar el registro de finalización como lock."""

    ruta_lock = f"{_ruta_base_real()}.migraciones.lock"
    descriptor = os.open(ruta_lock, os.O_CREAT | os.O_RDWR, 0o600)
    archivo = os.fdopen(descriptor, "a+")
    try:
        fcntl.flock(archivo.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(archivo.fileno(), fcntl.LOCK_UN)
        archivo.close()


def _nombres_objetos(conexion, tipo):
    return {
        fila[0]
        for fila in conexion.execute(
            "SELECT name FROM sqlite_master WHERE type = ?", (tipo,)
        ).fetchall()
    }


def _columnas_tabla(conexion, tabla):
    return {fila[1] for fila in conexion.execute(f"PRAGMA table_info({tabla})").fetchall()}


def _elementos_esquema(conexion, nombre):
    """Firma atómica de una migración: ``token -> está presente/correcto``."""

    requisitos = REQUISITOS_ESQUEMA.get(nombre)
    if requisitos is None:
        return {f"verificador:{nombre}": False}

    elementos = {}
    tablas = _nombres_objetos(conexion, "table")
    indices = _nombres_objetos(conexion, "index")
    for tabla in sorted(requisitos.get("tablas", set())):
        elementos[f"tabla:{tabla}"] = tabla in tablas
    for indice in sorted(requisitos.get("indices", set())):
        elementos[f"indice:{indice}"] = indice in indices
    for tabla, columnas in requisitos.get("columnas", {}).items():
        existentes = _columnas_tabla(conexion, tabla)
        for columna in sorted(columnas):
            elementos[f"columna:{tabla}.{columna}"] = columna in existentes

    if nombre == "agregar_calculo_compra":
        conversiones = {}
        if "conversiones_unidad" in tablas:
            conversiones = {
                fila[0]: (fila[1], fila[2], fila[3], fila[4])
                for fila in conexion.execute(
                    """
                    SELECT unidad_origen, unidad_canonica, factor_a_canonica, dimension, activa
                    FROM conversiones_unidad
                    """
                ).fetchall()
            }
        for origen, canonica, factor, dimension in _m_calculo_compra.UNIDADES:
            elementos[f"conversion:{origen}"] = (
                conversiones.get(origen) == (canonica, factor, dimension, 1)
            )
    return elementos


def _faltantes_esquema(conexion, nombre):
    return [
        token
        for token, presente in _elementos_esquema(conexion, nombre).items()
        if not presente
    ]


def _estado_esquema(conexion, nombre):
    elementos = _elementos_esquema(conexion, nombre)
    presentes = sum(1 for presente in elementos.values() if presente)
    if presentes == len(elementos):
        return "completo"
    if presentes == 0:
        return "ausente"
    return "parcial"


def _tabla_seguimiento_existe(conexion):
    return "migraciones_aplicadas" in _nombres_objetos(conexion, "table")


def _tablas_aplicacion(conexion):
    return {
        nombre
        for nombre in _nombres_objetos(conexion, "table")
        if not nombre.startswith("sqlite_") and nombre != "migraciones_aplicadas"
    }


def _inicializar_registro_si_corresponde(conexion):
    """Crea el registro para una base nueva o adopta un baseline legacy.

    Una base legacy se adopta en una sola transacción y solo después de
    clasificar cada migración como completa o totalmente ausente. Cualquier
    firma parcial, o la ausencia del baseline histórico conocido, aborta sin
    crear ni poblar el registro.
    """

    if _tabla_seguimiento_existe(conexion):
        # El esquema fundacional se incorporó al registro después de que las
        # bases de producción ya tenían marcadores para las migraciones
        # históricas. Solo se adopta ese marcador cuando (a) el registro no
        # está vacío y (b) la firma completa de productos fue verificada.
        # Un registro vacío con esquema completo sigue siendo un mismatch de
        # interrupción y no se repara silenciosamente.
        nombre_fundacional = "crear_esquema_fundacional"
        tiene_marcadores = conexion.execute(
            "SELECT 1 FROM migraciones_aplicadas LIMIT 1"
        ).fetchone() is not None
        if tiene_marcadores and not _esta_aplicada(conexion, nombre_fundacional):
            estado = _estado_esquema(conexion, nombre_fundacional)
            if estado == "completo":
                _marcar_aplicada(conexion, nombre_fundacional)
                logger.info("REGISTRO_FUNDACIONAL_ADOPTADO esquema=productos")
            elif estado == "parcial":
                raise ErrorEsquema(
                    "ESQUEMA_FUNDACIONAL_INCONSISTENTE registro=existente esquema=parcial"
                )
        return "existente"

    if not _tablas_aplicacion(conexion):
        _asegurar_tabla_seguimiento(conexion)
        return "nueva"

    estados = {
        nombre: _estado_esquema(conexion, nombre)
        for nombre, _ in MIGRACIONES
    }
    parciales = sorted(nombre for nombre, estado in estados.items() if estado == "parcial")
    if parciales:
        raise ErrorEsquema(
            "BASE_LEGACY_INCONSISTENTE migraciones_parciales=" + ",".join(parciales)
        )

    baseline_incompleta = sorted(
        nombre
        for nombre in BASELINE_LEGACY_REQUERIDA
        if estados.get(nombre) != "completo"
    )
    if baseline_incompleta:
        raise ErrorEsquema(
            "BASE_LEGACY_DESCONOCIDA baseline_incompleta=" + ",".join(baseline_incompleta)
        )

    try:
        conexion.execute(
            """
            CREATE TABLE migraciones_aplicadas (
                nombre TEXT PRIMARY KEY,
                fecha_aplicada TEXT NOT NULL
            )
            """
        )
        fecha = time.strftime("%Y-%m-%d %H:%M:%S")
        conexion.executemany(
            "INSERT INTO migraciones_aplicadas (nombre, fecha_aplicada) VALUES (?, ?)",
            [(nombre, fecha) for nombre, estado in estados.items() if estado == "completo"],
        )
        conexion.commit()
    except Exception:
        conexion.rollback()
        raise

    adoptadas = sorted(nombre for nombre, estado in estados.items() if estado == "completo")
    logger.info(f"REGISTRO_LEGACY_ADOPTADO migraciones={adoptadas}")
    return "legacy"


def _verificar_coherencia(conexion, nombre):
    registrada = _esta_aplicada(conexion, nombre)
    faltantes = _faltantes_esquema(conexion, nombre)
    esquema_completo = not faltantes
    if registrada != esquema_completo:
        estado_registro = "aplicada" if registrada else "ausente"
        estado_esquema = "completo" if esquema_completo else f"incompleto ({', '.join(faltantes)})"
        raise ErrorEsquema(
            f"REGISTRO_ESQUEMA_INCONSISTENTE migracion={nombre} "
            f"registro={estado_registro} esquema={estado_esquema}"
        )
    return registrada


def migraciones_completadas():
    """Nombres ya registrados en migraciones_aplicadas -- para diagnóstico
    (ver api/routers/auth.py: si /auth/registro se topa con "no such
    table: usuarios", esto dice exactamente cuáles de las registradas en
    MIGRACIONES sí llegaron a correr). Nunca lanza: si la tabla de
    seguimiento todavía ni existe
    (el propio arranque no llegó a asegurarla), devuelve una lista vacía
    en vez de fallar -- es información de diagnóstico, no debe agregar un
    segundo punto de falla."""
    try:
        conexion = conectar()
        try:
            filas = conexion.execute("SELECT nombre FROM migraciones_aplicadas ORDER BY fecha_aplicada").fetchall()
        finally:
            conexion.close()
        return [fila["nombre"] for fila in filas]
    except Exception:
        return []


def _procesar_una(nombre, funcion):
    conexion = conectar()
    try:
        aplicada = _verificar_coherencia(conexion, nombre)
    finally:
        conexion.close()

    if aplicada:
        logger.info(f"MIGRACION saltada nombre={nombre} -- ya estaba aplicada")
        return "saltada"

    logger.info(f"MIGRACION inicio nombre={nombre}")
    t0 = time.time()
    try:
        funcion()
    except Exception as error:
        duracion_ms = int((time.time() - t0) * 1000)
        logger.exception(f"MIGRACION fallo nombre={nombre} duracion_ms={duracion_ms}")
        raise ErrorEsquema(f"MIGRACION_FALLIDA nombre={nombre}") from error

    # El cuerpo ya retornó y, por contrato, su transacción terminó con commit.
    # Antes de registrar el éxito se comprueba el resultado real.
    conexion = conectar()
    try:
        faltantes = _faltantes_esquema(conexion, nombre)
        if faltantes:
            raise ErrorEsquema(
                f"MIGRACION_SIN_ESQUEMA nombre={nombre} faltantes={faltantes}"
            )
        _marcar_aplicada(conexion, nombre)
    finally:
        conexion.close()

    duracion_ms = int((time.time() - t0) * 1000)
    logger.info(f"MIGRACION completada nombre={nombre} duracion_ms={duracion_ms}")
    return "completada"


def aplicar_migraciones_pendientes():
    _asegurar_handler()

    # Punto de partida de todo diagnóstico futuro: cuál DATABASE_PATH usa
    # este proceso, y cuántas migraciones espera encontrar. Antes de esto
    # no había ninguna línea de log que lo confirmara.
    logger.info(
        f"RUNNER inicio database_path={db.BASE_DATOS} migraciones_registradas={len(MIGRACIONES)} "
        f"nombres={[nombre for nombre, _ in MIGRACIONES]}"
    )
    t_inicio_total = time.time()

    resultados = {}
    with _bloqueo_exclusivo_runner():
        conexion = conectar()
        try:
            _inicializar_registro_si_corresponde(conexion)
        finally:
            conexion.close()

        for nombre, funcion in MIGRACIONES:
            resultados[nombre] = _procesar_una(nombre, funcion)

        # Gate final de readiness: todos los registros deben concordar con
        # el esquema observable después de ejecutar/saltar la lista completa.
        conexion = conectar()
        try:
            for nombre, _ in MIGRACIONES:
                if not _verificar_coherencia(conexion, nombre):
                    raise ErrorEsquema(f"MIGRACION_NO_APLICADA nombre={nombre}")
        finally:
            conexion.close()

    duracion_total_ms = int((time.time() - t_inicio_total) * 1000)
    completadas_o_ya_aplicadas = sum(1 for r in resultados.values() if r in ("completada", "saltada"))
    total = len(MIGRACIONES)
    logger.info(
        f"RESUMEN {completadas_o_ya_aplicadas}/{total} migraciones aplicadas -- "
        f"esquema verificado duracion_total_ms={duracion_total_ms}"
    )
    logger.info(f"RUNNER fin duracion_total_ms={duracion_total_ms}")


if __name__ == "__main__":
    aplicar_migraciones_pendientes()
