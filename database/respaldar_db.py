"""Respaldo de database/proyecta.db (ver PRODUCTION_READINESS_REVIEW.md,
hallazgo B1: "no existe ningún mecanismo de respaldo en todo el repo" --
antes de este script, la única copia de seguridad que existió jamás fue
una copia manual de un desarrollador antes de correr una migración de
prueba, en un directorio de sesión efímero).

Esto NO resuelve el hallazgo B2 (durabilidad de los datos de producción a
través de un redeploy, que depende de qué plataforma de hosting se use y
cómo esté configurada -- algo que este repo no controla). Es la salvaguarda
mínima que sí cabe dentro de "corregir sin agregar funcionalidades": un
respaldo local, sin dependencias nuevas, para que exista *algún* camino de
recuperación mientras se resuelve el respaldo real de producción.

Usa `sqlite3.Connection.backup()` (equivalente al comando `.backup` de la
CLI de SQLite) en vez de copiar el archivo con `shutil.copy` -- una copia
de archivo plano puede capturar la base a mitad de una escritura si el
proceso de la API sigue corriendo (los archivos -wal/-shm no se copian con
ella); `.backup()` es seguro para copiar una base "en caliente", sin
detener nada.

Los respaldos se guardan junto a la propia base (mismo directorio que
BASE_DATOS, en una subcarpeta `respaldos/`) -- nunca hardcodeado a
`database/respaldos/`. Importa en producción: si BASE_DATOS apunta al
disco persistente (`/data/proyecta.db`), los respaldos quedan en
`/data/respaldos/`, en el mismo disco durable -- guardarlos en el
checkout efímero del repo (como hacía la versión anterior de este
script) los habría vuelto inútiles apenas se aplicara la migración a
`/data`: un redeploy los habría borrado a todos junto con el resto del
filesystem efímero. En local, sigue siendo `database/respaldos/` porque
ahí vive `database/proyecta.db` por defecto -- mismo comportamiento de
siempre. Nunca se versionan en git (ver .gitignore) -- versionar los
respaldos ahí sería repetir exactamente el mismo problema que tiene
database/proyecta.db (hallazgo B3), solo que multiplicado por cada
respaldo.

DIAGNOSTICO_DISCO_LLENO.md, causa raíz (disco productivo lleno,
1GB -> 2GB): esta versión anterior escribía la copia DIRECTAMENTE con el
nombre final (`proyecta_{timestamp}.db`) -- `sqlite3.connect(destino)`
crea ese archivo de inmediato, y si `.backup()` fallaba a mitad de copia
(ej. "database or disk is full"), no había ningún try/except que lo
capturara: la excepción se propagaba y el archivo destino, truncado o de
0 bytes, quedaba en el disco para siempre. `_purgar_respaldos_viejos()`
contaba TODOS los archivos que matcheaban `proyecta_*.db` para decidir
cuáles conservar, sin verificar que fueran respaldos usables -- un
archivo corrupto podía ocupar uno de los `mantener` cupos "protegidos"
indefinidamente, sin aportar ninguna capacidad real de recuperación. Y
como nunca se comprobaba espacio libre antes de arrancar, cada corrida
del bucle de 6 horas (ver api/main.py) podía repetir el mismo fallo,
dejando otro archivo corrupto más: un espiral de intentos fallidos que
nunca liberaba espacio y ocupaba cada vez un poco más, hasta llenar el
disco.

Esta versión escribe primero a un temporal oculto y exclusivo, comprueba
origen, espacio, copia, esquema, integridad y checksum, y solo entonces
publica mediante un `os.replace()` durable. La retención ocurre después de
publicar y solo considera copias comprobadas: nunca se borra un punto de
recuperación existente para intentar crear otro que todavía puede fallar.

Hallazgo posterior (revisión previa a mergear a producción): `.backup()`
copia el flag de journal_mode del origen tal cual, así que el temporal
quedaba marcado WAL aunque nadie se lo pidió -- cada `integrity_check`
posterior (acá mismo, o en cada purga futura sobre CUALQUIER respaldo
histórico ya marcado WAL) le creaba `-shm`/`-wal` huérfanos, ya que
`os.replace()` solo renombra el archivo base, nunca sus sidecars. Dos
capas de arreglo, no una sola: `_copiar_online()` normaliza el temporal a
`journal_mode=DELETE` inmediatamente después de `.backup()` (nunca antes
-- backup() pisaría el cambio), y toda inspección de un archivo SQLite
estático (temporal ya cerrado, o un respaldo histórico durante la
retención) usa `immutable=1` -- así ni siquiera un archivo heredado de
antes de este fix, todavía marcado WAL, genera sidecars nuevos al
validarlo. La base viva de origen nunca usa `immutable=1` (sí cambia).

Uso:
    PYTHONPATH=. .venv/bin/python3 database/respaldar_db.py
    PYTHONPATH=. .venv/bin/python3 database/respaldar_db.py --mantener 10

Para automatizar (cron, launchd, o el scheduler de la plataforma de
despliegue): correr este comando periódicamente. Este script no configura
ningún cron por sí mismo -- ver DEPLOYMENT.md.
"""

import argparse
import fcntl
import hashlib
import math
import os
import re
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

from db import BASE_DATOS

MARGEN_SEGURIDAD_BYTES = 64 * 1024 * 1024
MARGEN_SEGURIDAD_PROPORCION = 0.25
ARCHIVO_LOCK = ".respaldo_sqlite.lock"

# Códigos de salida de respaldar(): api/main.py los registra como éxito,
# fallo u omisión, y la CLI los propaga como exit code. El valor 2 distingue
# "no se ejecutó porque otra corrida tiene el lock" (transitorio) de éxito
# real (0) y fallo (1).
CODIGO_LOCK_OCUPADO = 2

# Nombres publicados por Zentra. La primera forma corresponde al script
# histórico, que solo incluía precisión a segundos. La segunda es la forma
# actual: microsegundos + UUID para que dos ejecuciones nunca compartan ruta.
# Cualquier otro nombre se conserva físicamente, pero no cuenta para retención.
_PATRON_RESPALDO_SEGUNDOS = re.compile(
    r"^proyecta_(?P<fecha>\d{8}_\d{6})\.db$"
)
_PATRON_RESPALDO_MICROSEGUNDOS = re.compile(
    r"^proyecta_(?P<fecha>\d{8}_\d{6})_(?P<microsegundos>\d{6})_"
    r"(?P<uuid>[0-9a-f]{32})\.db$"
)

# Estas tres tablas existen desde el esquema fundacional de Zentra. Un
# respaldo histórico puede carecer de tablas/columnas agregadas por migraciones
# posteriores y seguir siendo restaurable; por eso no se exige igualdad con el
# esquema actual durante la retención. Sí se exige igualdad exacta para el
# temporal recién creado, antes de publicarlo (ver _inspeccionar_sqlite()).
TABLAS_FUNDACIONALES_ZENTRA = frozenset({
    "productos",
    "proyectos",
    "items_proyecto",
})


def _uri_solo_lectura(ruta, inmutable=False):
    sufijo = "&immutable=1" if inmutable else ""
    return f"{Path(ruta).resolve().as_uri()}?mode=ro{sufijo}"


def _conectar_solo_lectura(ruta, inmutable=False):
    return sqlite3.connect(_uri_solo_lectura(ruta, inmutable=inmutable), uri=True)


def _huella_esquema(conexion):
    filas = conexion.execute(
        """
        SELECT type, name, tbl_name, COALESCE(sql, '')
        FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%'
          AND type IN ('table', 'view', 'index', 'trigger')
        ORDER BY type, name, tbl_name, COALESCE(sql, '')
        """
    ).fetchall()
    if not any(fila[0] == "table" for fila in filas):
        raise ValueError("la base SQLite no contiene tablas de usuario")
    return tuple(filas)


def _validar_conexion_sqlite(conexion):
    resultado = conexion.execute("PRAGMA integrity_check").fetchall()
    if resultado != [("ok",)]:
        raise ValueError("PRAGMA integrity_check no devolvió ok")
    return _huella_esquema(conexion)


def _inspeccionar_sqlite(ruta, huella_esperada=None):
    """Valida un archivo SQLite estático -- el temporal recién copiado
    (cerrado, nadie más lo toca) o un respaldo histórico ya publicado.
    Nunca se usa sobre la base viva de origen (esa usa su propia conexión
    en respaldar(), sin `immutable=1` -- ver ahí por qué).

    `immutable=1`: le dice a SQLite que el archivo no va a cambiar bajo
    ningún concepto mientras dure la conexión -- cierto acá porque ambos
    casos son archivos ya cerrados/publicados. Sin esto, abrir un archivo
    cuyo header quedó marcado WAL (heredado del origen vía `.backup()`, o
    de un respaldo histórico previo al fix del leak de sidecars) le crea
    `-shm`/`-wal` huérfanos en cada inspección -- exactamente lo que este
    modo evita, sin tocar ni un byte del contenido ni del resultado de
    integrity_check."""
    ruta = Path(ruta)
    if ruta.stat().st_size <= 0:
        raise ValueError("el archivo SQLite está vacío")
    conexion = _conectar_solo_lectura(ruta, inmutable=True)
    try:
        huella = _validar_conexion_sqlite(conexion)
    finally:
        conexion.close()
    if huella_esperada is not None and huella != huella_esperada:
        raise ValueError("el esquema del respaldo no coincide con el origen")
    return huella


def _timestamp_canonico(ruta):
    nombre = Path(ruta).name
    coincidencia = _PATRON_RESPALDO_SEGUNDOS.fullmatch(nombre)
    formato = "%Y%m%d_%H%M%S"
    texto = coincidencia.group("fecha") if coincidencia else None
    if coincidencia is None:
        coincidencia = _PATRON_RESPALDO_MICROSEGUNDOS.fullmatch(nombre)
        formato = "%Y%m%d_%H%M%S_%f"
        if coincidencia:
            texto = f"{coincidencia.group('fecha')}_{coincidencia.group('microsegundos')}"
    if coincidencia is None:
        return None
    try:
        return datetime.strptime(texto, formato)
    except ValueError:
        return None


def _tablas_de_huella(huella):
    return {fila[1] for fila in huella if fila[0] == "table"}


def _es_respaldo_valido(ruta):
    """Indica si una copia histórica es restaurable y elegible para retención.

    No exige el esquema actual exacto: una copia anterior a una migración sigue
    siendo restaurable. Exige nombre canónico, SQLite íntegra y las tablas que
    identifican el esquema fundacional de Zentra, evitando contar cualquier
    SQLite ajena que casualmente contenga una tabla.

    Antes de abrir con `immutable=1` (ver _inspeccionar_sqlite), revisa el
    -wal asociado. `immutable=1` le dice a SQLite que el archivo no va a
    cambiar bajo ningún concepto -- si el -wal existe y pesa más de 0
    bytes, hay una transacción confirmada que todavía no se plegó al
    archivo base, y SQLite no garantiza nada sobre lo que se lea en ese
    modo (podría ser una versión vieja de los datos, o un error). Ese
    respaldo se clasifica como no autónomo: no cuenta para retención, y
    -- al no entrar nunca en _respaldos_validos() -- tampoco se purga
    jamás, ni él ni sus sidecars, sin importar cuán ajustado esté
    `mantener`. Un -wal presente mide 0 bytes es harina de otro costal:
    no hay nada pendiente, el archivo base ya es autónomo y se valida
    normalmente con immutable=1 más abajo.
    """
    ruta = Path(ruta)
    if _timestamp_canonico(ruta) is None:
        return False
    wal, _shm = _sidecars_wal(ruta)
    if wal.exists() and wal.stat().st_size > 0:
        print(
            f"⚠️ Respaldo histórico {ruta.name}: WAL no vacío -- no es "
            "autónomo, se excluye de la retención automática (no cuenta "
            "como válido; no se purga ni él ni sus sidecars)."
        )
        return False
    try:
        huella = _inspeccionar_sqlite(ruta)
        return TABLAS_FUNDACIONALES_ZENTRA.issubset(_tablas_de_huella(huella))
    except (OSError, sqlite3.Error, ValueError):
        return False


def _respaldos_validos(directorio_respaldos):
    candidatos = []
    for candidato in Path(directorio_respaldos).glob("proyecta_*.db"):
        timestamp = _timestamp_canonico(candidato)
        if timestamp is not None and _es_respaldo_valido(candidato):
            candidatos.append((timestamp, candidato.name, candidato))
    return [candidato for _timestamp, _nombre, candidato in sorted(candidatos)]


def _espacio_libre(directorio):
    return shutil.disk_usage(directorio).free


def _espacio_necesario(tamano_estimado):
    margen = max(
        MARGEN_SEGURIDAD_BYTES,
        math.ceil(tamano_estimado * MARGEN_SEGURIDAD_PROPORCION),
    )
    return tamano_estimado + margen


def _calcular_sha256(ruta):
    digest = hashlib.sha256()
    with Path(ruta).open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _fsync_archivo(ruta):
    with Path(ruta).open("rb") as archivo:
        os.fsync(archivo.fileno())


def _fsync_directorio(ruta):
    descriptor = os.open(str(ruta), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sidecars_wal(ruta):
    return (Path(f"{ruta}-wal"), Path(f"{ruta}-shm"))


def _eliminar_temporal_propio(temporal):
    """Limpia únicamente rutas derivadas del temporal propio (nunca otro
    archivo). Cada ruta (base, -wal, -shm) se intenta en su propio
    try/except -- que falle borrar una no debe impedir intentar las
    otras dos (ej. el proceso muere justo entre borrar la base y sus
    sidecars; el próximo _eliminar_temporal_propio de OTRA corrida nunca
    va a intentar limpiar el nombre de esta, así que vale la pena que
    esta corrida se lleve puesto todo lo que pueda antes de salir)."""
    if temporal is None:
        return
    for ruta in (Path(temporal),) + _sidecars_wal(temporal):
        try:
            ruta.unlink(missing_ok=True)
        except OSError as error:
            print(
                f"❌ No se pudo limpiar {ruta.name} del temporal propio: "
                f"{type(error).__name__}"
            )


def _copiar_online(conexion_origen, temporal):
    conexion_destino = None
    try:
        conexion_destino = sqlite3.connect(str(temporal))
        conexion_origen.backup(conexion_destino)
        # .backup() copia la página 1 del origen tal cual -- incluido el
        # flag de journal_mode. Si el origen está en WAL (como la base
        # real, ver db.py), el temporal queda marcado WAL en su propio
        # header aunque nadie se lo pidió acá. Cualquier apertura
        # posterior (integrity_check de este mismo temporal, o su futura
        # validación como respaldo publicado en cada purga) le crearía
        # -shm/-wal huérfanos -- os.replace() más abajo solo renombra el
        # archivo base, nunca sus sidecars, así que quedarían huérfanos
        # bajo el nombre temporal descartado para siempre. Normalizar acá
        # DESPUÉS de backup() (antes no sirve -- backup() pisaría
        # cualquier cambio previo al copiar la página 1) deja la copia en
        # modo journal normal, sin sidecars, sin cambiar ni un byte del
        # contenido.
        modo = conexion_destino.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if modo != "delete":
            raise ValueError(
                f"no se pudo normalizar journal_mode a delete (quedó en {modo!r})"
            )
        conexion_destino.commit()
    finally:
        if conexion_destino is not None:
            conexion_destino.close()


def respaldar(mantener=20):
    origen = Path(BASE_DATOS)
    archivo_lock = None
    lock_adquirido = False
    conexion_origen = None
    temporal = None
    publicado = False
    etapa = "origen"
    try:
        archivo_lock = (origen.parent / ARCHIVO_LOCK).open("a+b")
        try:
            fcntl.flock(archivo_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                "⚠️ Respaldo omitido: otra ejecución mantiene el lock "
                f"({ARCHIVO_LOCK}) -- no se creó ni purgó nada en esta corrida."
            )
            return CODIGO_LOCK_OCUPADO
        lock_adquirido = True

        if not origen.exists():
            print(f"❌ Falló el respaldo etapa=origen: no existe {origen}.")
            return 1

        conexion_origen = _conectar_solo_lectura(origen)
        conexion_origen.execute("PRAGMA busy_timeout = 10000")
        # PRAGMA integrity_check corre completo contra el ORIGEN acá (no
        # solo contra la copia, más abajo) -- recorre todo el B-tree de la
        # base real de producción en cada ciclo de 6 horas. Pendiente:
        # medir su duración real contra la base productiva durante el
        # primer ciclo en vivo, para confirmar que sigue siendo
        # despreciable frente al intervalo entre corridas y que no compite
        # de forma perceptible con las escrituras normales de la API.
        huella_origen = _validar_conexion_sqlite(conexion_origen)
        pagina = conexion_origen.execute("PRAGMA page_size").fetchone()[0]
        paginas = conexion_origen.execute("PRAGMA page_count").fetchone()[0]
        tamano_estimado = max(origen.stat().st_size, pagina * paginas)
        etapa = "espacio"
        necesario = _espacio_necesario(tamano_estimado)
        libre = _espacio_libre(origen.parent)
        # Aborta ANTES de crear cualquier archivo -- a propósito, nunca purga
        # respaldos existentes para "hacer espacio" a uno nuevo que todavía
        # puede fallar (ver _purgar_respaldos_viejos: la purga solo corre
        # después de publicar un respaldo válido). Si esta condición se
        # repite, es una señal de que el disco necesita intervención
        # operativa real -- ampliarlo o liberar espacio a mano -- no algo de
        # lo que este script se recupere solo.
        if libre < necesario:
            print(
                "❌ Falló el respaldo etapa=espacio: espacio insuficiente "
                "para respaldar de forma segura "
                f"(libres: {libre} bytes, necesarios: {necesario} bytes); "
                "no se creó temporal ni se purgó ningún respaldo. Esto "
                "requiere intervención operativa (liberar espacio en disco "
                "o ampliarlo) -- este script no purga automáticamente para "
                "recuperarse por sí mismo."
            )
            return 1

        directorio_respaldos = origen.parent / "respaldos"
        directorio_respaldos.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        identificador = uuid.uuid4().hex
        temporal = directorio_respaldos / f".proyecta_{timestamp}_{identificador}.db.tmp"
        destino = directorio_respaldos / f"proyecta_{timestamp}_{identificador}.db"

        etapa = "copia"
        _copiar_online(conexion_origen, temporal)
        conexion_origen.close()
        conexion_origen = None

        etapa = "validacion"
        tamano_temporal = temporal.stat().st_size
        minimo_razonable = max(pagina, tamano_estimado // 2)
        maximo_razonable = tamano_estimado + max(
            MARGEN_SEGURIDAD_BYTES,
            math.ceil(tamano_estimado * MARGEN_SEGURIDAD_PROPORCION),
        )
        if not minimo_razonable <= tamano_temporal <= maximo_razonable:
            raise ValueError("el tamaño del respaldo no es razonable")

        _inspeccionar_sqlite(temporal, huella_esperada=huella_origen)
        etapa = "sha"
        sha256 = _calcular_sha256(temporal)
        etapa = "fsync"
        _fsync_archivo(temporal)
        etapa = "publicacion"
        os.replace(temporal, destino)
        publicado = True
        temporal = None
        etapa = "fsync"
        _fsync_directorio(directorio_respaldos)

        tamano_mb = destino.stat().st_size / (1024 * 1024)
        print(f"✅ Respaldo creado: {destino} ({tamano_mb:.1f} MB, sha256={sha256})")
        etapa = "purga"
        retencion_completa = _purgar_respaldos_viejos(
            directorio_respaldos,
            mantener,
            respaldo_nuevo=destino,
        )
        if not retencion_completa:
            print("⚠️ Respaldo correcto, etapa=purga retención incompleta.")
        return 0
    except (KeyboardInterrupt, SystemExit):
        _eliminar_temporal_propio(temporal)
        raise
    except Exception as error:
        _eliminar_temporal_propio(temporal)
        estado = "después de publicar" if publicado else "antes de publicar"
        print(
            f"❌ Falló el respaldo etapa={etapa} {estado}: "
            f"{type(error).__name__}"
        )
        return 1
    finally:
        if conexion_origen is not None:
            conexion_origen.close()
        if lock_adquirido:
            fcntl.flock(archivo_lock.fileno(), fcntl.LOCK_UN)
        if archivo_lock is not None:
            archivo_lock.close()


def _purgar_respaldos_viejos(directorio_respaldos, mantener, respaldo_nuevo):
    """Purga históricos restaurables sin eliminar la copia recién publicada."""
    if mantener <= 0:
        return True
    respaldo_nuevo = Path(respaldo_nuevo).resolve()
    historicos = [
        respaldo
        for respaldo in _respaldos_validos(directorio_respaldos)
        if respaldo.resolve() != respaldo_nuevo
    ]
    cupos_historicos = max(mantener - 1, 0)
    cantidad_a_eliminar = max(len(historicos) - cupos_historicos, 0)
    de_mas = historicos[:cantidad_a_eliminar]
    for respaldo in de_mas:
        # Base, -wal y -shm se intentan de forma independiente -- que
        # falle borrar una no debe impedir intentar las otras dos (ej. la
        # base se borra bien pero el -wal falla por permisos: igual hay
        # que intentar el -shm). Sidecars del MISMO respaldo que se está
        # purgando, nunca de otro archivo. Pueden existir por haber
        # quedado marcados WAL antes de este fix (ver _copiar_online), o
        # por haber sido inspeccionados en una purga anterior a este fix.
        hubo_fallo = False
        for ruta in (respaldo,) + _sidecars_wal(respaldo):
            try:
                ruta.unlink(missing_ok=True)
            except OSError as error:
                hubo_fallo = True
                print(
                    f"  - no se pudo borrar {ruta.name} del respaldo viejo "
                    f"{respaldo.name}: {type(error).__name__}"
                )
        if hubo_fallo:
            # Se detiene la purga acá -- no se intenta el siguiente
            # respaldo de de_mas. respaldar() reporta retención
            # incompleta con este False.
            return False
        print(f"  - se borró el respaldo viejo {respaldo.name} (se mantienen los últimos {mantener})")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mantener", type=int, default=20,
        help="cuántos respaldos válidos recientes conservar (0 = no purgar ninguno). Por defecto: 20.",
    )
    args = parser.parse_args()
    sys.exit(respaldar(mantener=args.mantener))
