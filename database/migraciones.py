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
- Tabla `migraciones_aplicadas(nombre PRIMARY KEY)` -- por migración, no
  un flag global: no sabemos desde acá cuáles de las 8 ya corrieron en la
  base real de producción (probablemente 7 de 8 sí, solo la de
  autenticación falta), así que cada una se decide de forma independiente.
- Reclamo atómico con `INSERT OR IGNORE`: con `--workers 4` (ver
  render.yaml), 4 procesos disparan su propio `on_event("startup")` casi
  al mismo tiempo. SQLite serializa esas escrituras (WAL + busy_timeout ya
  configurados en db.py) -- de las 4 inserciones concurrentes para el
  mismo `nombre`, solo UNA agrega la fila de verdad (`rowcount == 1`).
  Esa es la única instancia que llama el cuerpo real de la migración; las
  otras 3 ven `rowcount == 0` y no hacen nada. Sin esto, los 4 workers
  ejecutarían cada migración 4 veces en paralelo.
- Si el cuerpo de una migración lanza una excepción, se borra su propio
  reclamo antes de seguir -- así el próximo arranque la reintenta, en vez
  de quedar marcada "aplicada" para siempre sin haber terminado. Además,
  cada migración corre dentro de un try/except propio a nivel del ciclo
  completo (ver `aplicar_migraciones_pendientes`): si una falla de forma
  irrecuperable (se encontró un caso real -- ver más abajo), el resto del
  registro se sigue intentando igual, nunca se corta la lista entera por
  una sola migración rota.
- Reutiliza el `main()` de cada `agregar_*.py` tal cual -- este módulo no
  reescribe ni un renglón de la lógica de ninguna migración existente.

Verificado en vivo, no solo con pruebas unitarias con dobles de prueba
(`tests/test_migraciones.py`): (1) contra un archivo vacío de verdad, para
forzar fallas reales en cascada -- encontró un bug real y preexistente en
`busqueda.reconstruir_indice()` (no cierra su conexión si falla entre su
INSERT y su SELECT, dejando una transacción abierta el resto del proceso;
fuera de alcance arreglar, es lógica de una migración existente, pero el
runner ya no se cae por eso -- registra el error y sigue con las
siguientes); (2) contra una copia real de database/proyecta.db con
`usuarios`/`sesiones`/`feedback` eliminadas a propósito (el escenario real
del bug) -- las 8 corrieron limpio, el catálogo (60,421 productos) y los
17 proyectos existentes quedaron intactos, y `registrar_usuario()` empezó
a funcionar de inmediato; correrlo una segunda vez saltó las 8 en 0.18s.

Costo conocido, sin resolver a propósito (fuera del alcance pedido): la
tabla de seguimiento empieza vacía, así que el primer arranque con este
runner intenta aplicar todas las registradas. Para las basadas en
`CREATE ... IF NOT EXISTS` / `ALTER TABLE` con guard por columna,
reintentar contra una base que ya las tiene es gratis. Pero `agregar_equivalencias.py` y
`agregar_indice_busqueda.py` recalculan sin condición, sin importar si el
resultado ya existe -- así están escritas, y no se les cambió la lógica.
Ese primer arranque puede tardar varios minutos en el único worker que
gane el reclamo de esas dos. Corre en un hilo de fondo (ver
`api/main.py`), así que no bloquea que el proceso empiece a responder.
"""

import time

import db
from db import conectar
from api.observabilidad import _asegurar_handler, logger

import database.agregar_familias_producto as _m_familias_producto
import database.agregar_indice_busqueda as _m_indice_busqueda
import database.agregar_proyectos as _m_proyectos
import database.agregar_cotizaciones as _m_cotizaciones
import database.agregar_equivalencias as _m_equivalencias
import database.agregar_plano_proyecto as _m_plano_proyecto
import database.agregar_trazabilidad_items as _m_trazabilidad_items
import database.agregar_autenticacion as _m_autenticacion
import database.agregar_seleccion_automatica as _m_seleccion_automatica

# agregar_autenticacion primero, a propósito, fuera del orden histórico
# real de introducción: no depende de proyectos/productos/nada de las
# otras 7 (solo crea usuarios/sesiones/feedback, tablas propias que nadie
# más referencia), y es la que un incidente real de producción necesitaba
# ver aplicada cuanto antes. Dejarla última significaba que quedaba
# atrapada detrás de agregar_equivalencias (~10s recalculando el catálogo
# completo, más en Render que en local) y de cualquier falla de las
# primeras 7 -- sin ninguna razón real para depender de ese orden.
#
# El resto conserva su orden histórico real de introducción (ver git log):
# importa porque una migración posterior altera una tabla que una anterior
# crea (ej. agregar_cotizaciones.py hace ALTER TABLE sobre proyectos e
# items_proyecto, creadas por agregar_proyectos.py).
MIGRACIONES = [
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
]


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


def _reclamar(conexion, nombre):
    """Intenta ser la instancia que ejecuta `nombre`. True solo para la
    primera llamada (de cualquier worker) que logra insertar la fila --
    el resto de los workers, concurrentes o de un arranque posterior,
    reciben False y no hacen nada."""
    cursor = conexion.execute(
        "INSERT OR IGNORE INTO migraciones_aplicadas (nombre, fecha_aplicada) VALUES (?, ?)",
        (nombre, time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conexion.commit()
    return cursor.rowcount == 1


def _liberar(conexion, nombre):
    conexion.execute("DELETE FROM migraciones_aplicadas WHERE nombre = ?", (nombre,))
    conexion.commit()


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
    # Una conexión nueva y corta por paso (reclamo, y liberar si hace
    # falta) -- no se mantiene una conexión propia abierta mientras corre
    # el cuerpo de la migración. Así, si esa migración deja algo sin
    # cerrar (caso real encontrado: busqueda.reconstruir_indice() no
    # cierra su conexión si falla entre su INSERT y su SELECT, dejando
    # una transacción abierta), lo único afectado es la conexión de ESE
    # paso, nunca la nuestra.
    conexion = conectar()
    try:
        reclamada = _reclamar(conexion, nombre)
    finally:
        conexion.close()

    if not reclamada:
        logger.info(f"MIGRACION saltada nombre={nombre} -- ya estaba aplicada")
        return "saltada"

    logger.info(f"MIGRACION inicio nombre={nombre}")
    t0 = time.time()
    try:
        funcion()
    except Exception:
        duracion_ms = int((time.time() - t0) * 1000)
        logger.exception(f"MIGRACION fallo nombre={nombre} duracion_ms={duracion_ms}")
        conexion = conectar()
        try:
            _liberar(conexion, nombre)
        finally:
            conexion.close()
        logger.info(f"MIGRACION reclamo liberado nombre={nombre} -- se reintentará en el próximo arranque")
        return "fallida"

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

    conexion = conectar()
    _asegurar_tabla_seguimiento(conexion)
    conexion.close()

    resultados = {}
    for nombre, funcion in MIGRACIONES:
        try:
            resultados[nombre] = _procesar_una(nombre, funcion)
        except Exception:
            # Red de seguridad final: si CUALQUIER paso de esta migración
            # -- incluido reclamar o liberar -- lanza algo no previsto
            # (ej. un candado que otra migración dejó pegado en este
            # mismo proceso), esa migración se da por no resuelta esta
            # vez, pero el resto del registro sigue intentándose. Un
            # reinicio del proceso alcanza para reintentarla limpio --
            # cada worker nuevo arranca sin ningún candado heredado.
            logger.exception(
                f"MIGRACION error irrecuperable nombre={nombre} -- se sigue con las siguientes de la lista; "
                "un reinicio del proceso la reintentará"
            )
            resultados[nombre] = "error_irrecuperable"

    duracion_total_ms = int((time.time() - t_inicio_total) * 1000)
    completadas_o_ya_aplicadas = sum(1 for r in resultados.values() if r in ("completada", "saltada"))
    fallidas = [nombre for nombre, r in resultados.items() if r not in ("completada", "saltada")]
    total = len(MIGRACIONES)

    # La línea a buscar en los logs para saber, de un vistazo, si el
    # esquema quedó consistente: "RESUMEN N/N" significa que las N
    # migraciones registradas están aplicadas (ahora o de antes). Un
    # RESUMEN por debajo de N nombra exactamente cuáles faltan.
    if fallidas:
        logger.error(
            f"RESUMEN {completadas_o_ya_aplicadas}/{total} migraciones aplicadas -- "
            f"pendientes={fallidas} duracion_total_ms={duracion_total_ms}"
        )
    else:
        logger.info(
            f"RESUMEN {completadas_o_ya_aplicadas}/{total} migraciones aplicadas -- "
            f"todas al día duracion_total_ms={duracion_total_ms}"
        )
    logger.info(f"RUNNER fin duracion_total_ms={duracion_total_ms}")


if __name__ == "__main__":
    aplicar_migraciones_pendientes()
