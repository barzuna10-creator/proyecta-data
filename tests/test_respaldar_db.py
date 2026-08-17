"""Prueba para database/respaldar_db.py -- ver la migración a
/data/proyecta.db: antes, los respaldos vivían en un directorio
hardcodeado (database/respaldos/), sin importar dónde estuviera la base
real. Si BASE_DATOS apunta al disco persistente de Render, guardar los
respaldos en el checkout efímero del repo los habría vuelto inútiles --
un redeploy los borra a todos. Ahora el directorio de respaldos se deriva
de BASE_DATOS (mismo directorio, subcarpeta respaldos/), así que sigue a
la base real esté donde esté."""

import fcntl
import io
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import database.respaldar_db as respaldar_db


def _crear_esquema_fundacional(conexion):
    conexion.execute("CREATE TABLE productos (id INTEGER PRIMARY KEY, nombre TEXT)")
    conexion.execute("CREATE TABLE proyectos (id INTEGER PRIMARY KEY, nombre TEXT)")
    conexion.execute(
        "CREATE TABLE items_proyecto (id INTEGER PRIMARY KEY, proyecto_id INTEGER)"
    )


class PruebaRespaldar(unittest.TestCase):
    def setUp(self):
        self.directorio = tempfile.mkdtemp()
        self.ruta_db = os.path.join(self.directorio, "proyecta.db")
        conexion = sqlite3.connect(self.ruta_db)
        _crear_esquema_fundacional(conexion)
        conexion.execute("INSERT INTO productos (nombre) VALUES ('cemento')")
        conexion.commit()
        conexion.close()
        self._patch = mock.patch.object(respaldar_db, "BASE_DATOS", self.ruta_db)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_el_respaldo_queda_junto_a_la_base_real_no_en_una_ruta_fija(self):
        codigo = respaldar_db.respaldar()
        self.assertEqual(codigo, 0)

        directorio_respaldos = os.path.join(self.directorio, "respaldos")
        self.assertTrue(os.path.isdir(directorio_respaldos))
        archivos = os.listdir(directorio_respaldos)
        self.assertEqual(len(archivos), 1)
        self.assertTrue(archivos[0].startswith("proyecta_") and archivos[0].endswith(".db"))

        conexion = sqlite3.connect(os.path.join(directorio_respaldos, archivos[0]))
        fila = conexion.execute("SELECT nombre FROM productos").fetchone()
        conexion.close()
        self.assertEqual(fila[0], "cemento")

    def test_no_hardcodea_database_respaldos(self):
        ruta_real = "database/respaldos"
        antes = set(os.listdir(ruta_real)) if os.path.isdir(ruta_real) else set()

        respaldar_db.respaldar()

        despues = set(os.listdir(ruta_real)) if os.path.isdir(ruta_real) else set()
        self.assertEqual(
            antes, despues,
            "el respaldo cayó en database/respaldos aunque BASE_DATOS apuntaba a otro directorio",
        )

    def test_purga_respaldos_viejos_en_el_directorio_correcto(self):
        for _ in range(3):
            respaldar_db.respaldar(mantener=2)
        directorio_respaldos = os.path.join(self.directorio, "respaldos")
        archivos = os.listdir(directorio_respaldos)
        self.assertLessEqual(len(archivos), 2)

    def test_sin_base_no_falla_no_crea_directorio(self):
        self._patch.stop()
        self._patch = mock.patch.object(respaldar_db, "BASE_DATOS", os.path.join(self.directorio, "no_existe.db"))
        self._patch.start()
        codigo = respaldar_db.respaldar()
        self.assertEqual(codigo, 1)


class _BaseRespaldarRobusto(unittest.TestCase):
    def setUp(self):
        self.temporal_raiz = tempfile.TemporaryDirectory()
        self.directorio = self.temporal_raiz.name
        self.ruta_db = Path(self.directorio) / "proyecta.db"
        conexion = sqlite3.connect(self.ruta_db)
        _crear_esquema_fundacional(conexion)
        conexion.execute("INSERT INTO productos (nombre) VALUES ('cemento')")
        conexion.commit()
        conexion.close()
        self.directorio_respaldos = Path(self.directorio) / "respaldos"
        self._patch = mock.patch.object(respaldar_db, "BASE_DATOS", str(self.ruta_db))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.temporal_raiz.cleanup()

    def _dbs(self):
        if not self.directorio_respaldos.exists():
            return []
        return sorted(self.directorio_respaldos.glob("proyecta_*.db"))

    def _temporales(self):
        if not self.directorio_respaldos.exists():
            return []
        return sorted(self.directorio_respaldos.glob(".proyecta_*.db.tmp"))

    def _crear_respaldo_valido(self, nombre):
        self.directorio_respaldos.mkdir(exist_ok=True)
        destino = self.directorio_respaldos / nombre
        origen = sqlite3.connect(self.ruta_db)
        copia = sqlite3.connect(destino)
        origen.backup(copia)
        copia.close()
        origen.close()
        return destino

    def _crear_sqlite_ajena(self, nombre):
        self.directorio_respaldos.mkdir(exist_ok=True)
        destino = self.directorio_respaldos / nombre
        conexion = sqlite3.connect(destino)
        conexion.execute("CREATE TABLE completamente_ajena (id INTEGER PRIMARY KEY)")
        conexion.commit()
        conexion.close()
        return destino

    def _crear_temporal_durante_fallo(self, temporal, error):
        Path(temporal).touch()
        raise error


class PruebaCalculoYValidacion(_BaseRespaldarRobusto):
    def test_margen_minimo_y_proporcional(self):
        mib64 = 64 * 1024 * 1024
        self.assertEqual(respaldar_db._espacio_necesario(1000), 1000 + mib64)
        gib = 1024 * 1024 * 1024
        self.assertEqual(respaldar_db._espacio_necesario(gib), gib + gib // 4)

    def test_respaldo_exitoso_es_readonly_integro_y_con_esquema_equivalente(self):
        sha_real = respaldar_db._calcular_sha256
        with mock.patch.object(respaldar_db, "_calcular_sha256", wraps=sha_real) as sha:
            self.assertEqual(respaldar_db.respaldar(), 0)
        self.assertEqual(len(self._dbs()), 1)
        self.assertEqual(self._temporales(), [])
        origen = respaldar_db._inspeccionar_sqlite(self.ruta_db)
        copia = respaldar_db._inspeccionar_sqlite(self._dbs()[0])
        self.assertEqual(origen, copia)
        self.assertEqual(sha.call_count, 1)
        self.assertEqual(len(respaldar_db._calcular_sha256(self._dbs()[0])), 64)
        conexion = respaldar_db._conectar_solo_lectura(self._dbs()[0])
        with self.assertRaises(sqlite3.OperationalError):
            conexion.execute("CREATE TABLE no_permitida (id INTEGER)")
        conexion.close()

    def test_sha256_con_valor_conocido(self):
        archivo = Path(self.directorio) / "abc.bin"
        archivo.write_bytes(b"abc")
        self.assertEqual(
            respaldar_db._calcular_sha256(archivo),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_origen_corrupto_aborta_sin_temporal(self):
        self.ruta_db.write_bytes(b"esto no es sqlite")
        salida = io.StringIO()
        with redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertIn("etapa=origen", salida.getvalue())
        self.assertEqual(self._dbs(), [])
        self.assertEqual(self._temporales(), [])

    def test_origen_sqlite_vacio_no_es_valido(self):
        self.ruta_db.unlink()
        sqlite3.connect(self.ruta_db).close()
        self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertEqual(self._dbs(), [])

    def test_temporal_sqlite_vacio_no_se_publica(self):
        def crear_vacio(_conexion, temporal):
            sqlite3.connect(temporal).close()

        with mock.patch.object(respaldar_db, "_copiar_online", side_effect=crear_vacio):
            self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertEqual(self._dbs(), [])
        self.assertEqual(self._temporales(), [])

    def test_esquema_distinto_no_se_publica(self):
        def crear_otro_esquema(_conexion, temporal):
            copia = sqlite3.connect(temporal)
            copia.execute("CREATE TABLE otra (id INTEGER PRIMARY KEY)")
            copia.commit()
            copia.close()

        with mock.patch.object(respaldar_db, "_copiar_online", side_effect=crear_otro_esquema):
            self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertEqual(self._dbs(), [])
        self.assertEqual(self._temporales(), [])


class PruebaEspacioYOrden(_BaseRespaldarRobusto):
    def test_espacio_insuficiente_no_crea_temporal_ni_purga(self):
        anteriores = [
            self._crear_respaldo_valido(f"proyecta_20200101_00000{i}.db")
            for i in range(2)
        ]
        snapshots = {ruta: ruta.read_bytes() for ruta in anteriores}
        salida = io.StringIO()
        with mock.patch.object(respaldar_db, "_espacio_libre", return_value=0), \
             mock.patch.object(respaldar_db, "_purgar_respaldos_viejos") as purga, \
             redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertIn("etapa=espacio", salida.getvalue())
        purga.assert_not_called()
        self.assertEqual(self._temporales(), [])
        self.assertEqual({ruta: ruta.read_bytes() for ruta in anteriores}, snapshots)

    def test_page_count_por_page_size_participa_en_el_preflight(self):
        vistos = []

        def necesario(tamano):
            vistos.append(tamano)
            return 1

        conexion = sqlite3.connect(self.ruta_db)
        esperado_logico = conexion.execute("PRAGMA page_count").fetchone()[0] * conexion.execute(
            "PRAGMA page_size"
        ).fetchone()[0]
        conexion.close()
        with mock.patch.object(respaldar_db, "_espacio_necesario", side_effect=necesario):
            self.assertEqual(respaldar_db.respaldar(), 0)
        self.assertEqual(vistos, [max(self.ruta_db.stat().st_size, esperado_logico)])


class PruebaFallosDePublicacion(_BaseRespaldarRobusto):
    def test_fallo_de_copia_limpia_solo_temporal_propio(self):
        ajeno = self.directorio_respaldos / ".proyecta_ajeno.db.tmp"
        self.directorio_respaldos.mkdir(exist_ok=True)
        ajeno.write_bytes(b"ajeno")
        efecto = lambda conexion, temporal: self._crear_temporal_durante_fallo(
            temporal, sqlite3.OperationalError("disk full")
        )
        salida = io.StringIO()
        with mock.patch.object(respaldar_db, "_copiar_online", side_effect=efecto), \
             mock.patch.object(respaldar_db, "_purgar_respaldos_viejos") as purga, \
             redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertIn("etapa=copia", salida.getvalue())
        purga.assert_not_called()
        self.assertEqual(ajeno.read_bytes(), b"ajeno")
        self.assertEqual(self._temporales(), [ajeno])

    def test_integrity_check_fallido_limpia_temporal(self):
        inspeccion_real = respaldar_db._inspeccionar_sqlite

        def falla_temporal(ruta, huella_esperada=None):
            if str(ruta).endswith(".tmp"):
                raise ValueError("integridad simulada")
            return inspeccion_real(ruta, huella_esperada)

        salida = io.StringIO()
        with mock.patch.object(respaldar_db, "_inspeccionar_sqlite", side_effect=falla_temporal), \
             redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertIn("etapa=validacion", salida.getvalue())
        self.assertEqual(self._dbs(), [])
        self.assertEqual(self._temporales(), [])

    def test_fallo_de_sha_limpia_temporal(self):
        salida = io.StringIO()
        with mock.patch.object(respaldar_db, "_calcular_sha256", side_effect=OSError("sha")), \
             redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertIn("etapa=sha", salida.getvalue())
        self.assertEqual(self._dbs(), [])
        self.assertEqual(self._temporales(), [])

    def test_fallo_de_fsync_del_archivo_limpia_temporal(self):
        salida = io.StringIO()
        with mock.patch.object(respaldar_db, "_fsync_archivo", side_effect=OSError("fsync")), \
             redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertIn("etapa=fsync", salida.getvalue())
        self.assertEqual(self._dbs(), [])
        self.assertEqual(self._temporales(), [])

    def test_fallo_de_replace_no_publica_y_limpia_temporal(self):
        salida = io.StringIO()
        with mock.patch.object(os, "replace", side_effect=OSError("replace")), \
             redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertIn("etapa=publicacion", salida.getvalue())
        self.assertEqual(self._dbs(), [])
        self.assertEqual(self._temporales(), [])

    def test_fallo_de_fsync_directorio_conserva_publicado_y_no_purga(self):
        with mock.patch.object(respaldar_db, "_fsync_directorio", side_effect=OSError("fsync dir")), \
             mock.patch.object(respaldar_db, "_purgar_respaldos_viejos") as purga:
            self.assertEqual(respaldar_db.respaldar(), 1)
        purga.assert_not_called()
        self.assertEqual(len(self._dbs()), 1)
        self.assertTrue(respaldar_db._es_respaldo_valido(self._dbs()[0]))
        self.assertEqual(self._temporales(), [])

    def test_keyboard_interrupt_limpia_temporal_relanza_y_libera_lock(self):
        efecto = lambda conexion, temporal: self._crear_temporal_durante_fallo(
            temporal, KeyboardInterrupt()
        )
        with mock.patch.object(respaldar_db, "_copiar_online", side_effect=efecto):
            with self.assertRaises(KeyboardInterrupt):
                respaldar_db.respaldar()
        self.assertEqual(self._temporales(), [])
        self.assertEqual(respaldar_db.respaldar(), 0)

    def test_system_exit_limpia_temporal_relanza_y_libera_lock(self):
        efecto = lambda conexion, temporal: self._crear_temporal_durante_fallo(
            temporal, SystemExit(9)
        )
        with mock.patch.object(respaldar_db, "_copiar_online", side_effect=efecto):
            with self.assertRaises(SystemExit) as contexto:
                respaldar_db.respaldar()
        self.assertEqual(contexto.exception.code, 9)
        self.assertEqual(self._temporales(), [])
        self.assertEqual(respaldar_db.respaldar(), 0)

    def test_orden_durable_fsync_replace_fsync_directorio(self):
        orden = []
        replace_real = os.replace

        def fsync_archivo(_ruta):
            orden.append("fsync_temporal")

        def replace(origen, destino):
            orden.append("replace")
            replace_real(origen, destino)

        def fsync_directorio(_ruta):
            orden.append("fsync_directorio")

        with mock.patch.object(respaldar_db, "_fsync_archivo", side_effect=fsync_archivo), \
             mock.patch.object(os, "replace", side_effect=replace), \
             mock.patch.object(respaldar_db, "_fsync_directorio", side_effect=fsync_directorio):
            self.assertEqual(respaldar_db.respaldar(), 0)
        self.assertEqual(orden, ["fsync_temporal", "replace", "fsync_directorio"])


class PruebaLockYConcurrencia(_BaseRespaldarRobusto):
    def test_lock_ocupado_omite_sin_crear_archivos(self):
        ruta_lock = Path(self.directorio) / respaldar_db.ARCHIVO_LOCK
        with ruta_lock.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(respaldar_db.respaldar(), respaldar_db.CODIGO_LOCK_OCUPADO)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        self.assertEqual(self._dbs(), [])
        self.assertEqual(self._temporales(), [])

    def test_lockfile_preexistente_desbloqueado_permite_respaldar(self):
        ruta_lock = Path(self.directorio) / respaldar_db.ARCHIVO_LOCK
        ruta_lock.touch()
        self.assertEqual(respaldar_db.respaldar(), 0)
        self.assertEqual(len(self._dbs()), 1)

    def test_contrato_lock_ocupado_congelado(self):
        """Congela el contrato de respaldar() con el lock ocupado: código
        distinguible de éxito (0) y de fallo genérico (1), y un warning
        explícito -- antes de este ajuste, un lock ocupado devolvía 0, la
        misma señal que un respaldo exitoso, indistinguible para cualquier
        consumidor futuro que sí llegue a inspeccionar el código de salida."""
        self.assertEqual(respaldar_db.CODIGO_LOCK_OCUPADO, 2)
        self.assertNotIn(respaldar_db.CODIGO_LOCK_OCUPADO, (0, 1))

        ruta_lock = Path(self.directorio) / respaldar_db.ARCHIVO_LOCK
        salida = io.StringIO()
        with ruta_lock.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with redirect_stdout(salida):
                codigo = respaldar_db.respaldar()
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

        self.assertEqual(codigo, respaldar_db.CODIGO_LOCK_OCUPADO)
        mensaje = salida.getvalue()
        self.assertIn("otra ejecución", mensaje)
        self.assertIn(respaldar_db.ARCHIVO_LOCK, mensaje)
        self.assertEqual(self._dbs(), [])
        self.assertEqual(self._temporales(), [])

    def test_dos_respaldos_simultaneos_producen_una_sola_copia(self):
        dentro_del_lock = threading.Event()
        liberar = threading.Event()
        validar_real = respaldar_db._validar_conexion_sqlite
        resultados = []
        errores = []

        def validacion_controlada(conexion):
            if not dentro_del_lock.is_set():
                dentro_del_lock.set()
                if not liberar.wait(5):
                    raise AssertionError("timeout esperando liberar el primer respaldo")
            return validar_real(conexion)

        def ejecutar():
            try:
                resultados.append(respaldar_db.respaldar())
            except BaseException as error:
                errores.append(error)

        with mock.patch.object(respaldar_db, "_validar_conexion_sqlite", side_effect=validacion_controlada):
            primero = threading.Thread(target=ejecutar)
            segundo = threading.Thread(target=ejecutar)
            try:
                primero.start()
                self.assertTrue(dentro_del_lock.wait(5))
                segundo.start()
                segundo.join(5)
                self.assertFalse(segundo.is_alive(), "la segunda ejecución quedó bloqueada")
            finally:
                liberar.set()
                primero.join(5)
                segundo.join(5)
        self.assertEqual(errores, [])
        self.assertEqual(sorted(resultados), sorted([0, respaldar_db.CODIGO_LOCK_OCUPADO]))
        self.assertEqual(len(self._dbs()), 1)
        self.assertEqual(self._temporales(), [])


class PruebaRetencionSegura(_BaseRespaldarRobusto):
    def test_respaldo_21_se_publica_antes_de_purgar_el_mas_viejo(self):
        anteriores = [
            self._crear_respaldo_valido(f"proyecta_20200101_{i:06d}.db")
            for i in range(20)
        ]
        replace_real = os.replace
        observado = []

        def replace_observado(origen, destino):
            replace_real(origen, destino)
            observado.append(len(self._dbs()))

        with mock.patch.object(os, "replace", side_effect=replace_observado):
            self.assertEqual(respaldar_db.respaldar(mantener=20), 0)
        self.assertEqual(observado, [21])
        self.assertEqual(len(self._dbs()), 20)
        self.assertFalse(anteriores[0].exists())
        self.assertTrue(all(ruta.exists() for ruta in anteriores[1:]))

    def test_sqlite_ajena_integra_no_cuenta(self):
        ajena = self._crear_sqlite_ajena("otra.db")
        self.assertFalse(respaldar_db._es_respaldo_valido(ajena))

    def test_sqlite_ajena_proyecta_zzzz_no_cuenta(self):
        ajena = self._crear_sqlite_ajena("proyecta_zzzz.db")
        self.assertFalse(respaldar_db._es_respaldo_valido(ajena))

    def test_nombre_canonico_con_esquema_ajeno_no_cuenta(self):
        ajena = self._crear_sqlite_ajena("proyecta_20200101_000000.db")
        self.assertFalse(respaldar_db._es_respaldo_valido(ajena))

    def test_mantener_uno_protege_el_respaldo_recien_publicado(self):
        historico = self._crear_respaldo_valido("proyecta_20200101_000000.db")
        ajeno = self._crear_sqlite_ajena("proyecta_zzzz.db")
        self.assertEqual(respaldar_db.respaldar(mantener=1), 0)
        validos = respaldar_db._respaldos_validos(self.directorio_respaldos)
        self.assertEqual(len(validos), 1)
        self.assertNotEqual(validos[0], historico)
        self.assertTrue(validos[0].exists())
        self.assertTrue(ajeno.exists(), "los inválidos preexistentes no se eliminan")

    def test_nombres_canonicos_historico_y_nuevo_son_compatibles(self):
        historico = self._crear_respaldo_valido("proyecta_20200101_010203.db")
        nuevo = self._crear_respaldo_valido(
            "proyecta_20200101_010203_123456_0123456789abcdef0123456789abcdef.db"
        )
        self.assertTrue(respaldar_db._es_respaldo_valido(historico))
        self.assertTrue(respaldar_db._es_respaldo_valido(nuevo))
        self.assertEqual(
            respaldar_db._respaldos_validos(self.directorio_respaldos),
            [historico, nuevo],
        )

    def test_esquema_historico_anterior_sigue_siendo_restaurable(self):
        ruta = self.directorio_respaldos / "proyecta_20200101_000000.db"
        self.directorio_respaldos.mkdir(exist_ok=True)
        conexion = sqlite3.connect(ruta)
        conexion.execute("CREATE TABLE productos (id INTEGER PRIMARY KEY)")
        conexion.execute("CREATE TABLE proyectos (id INTEGER PRIMARY KEY)")
        conexion.execute("CREATE TABLE items_proyecto (id INTEGER PRIMARY KEY)")
        conexion.commit()
        conexion.close()
        self.assertNotEqual(
            respaldar_db._inspeccionar_sqlite(ruta),
            respaldar_db._inspeccionar_sqlite(self.ruta_db),
        )
        self.assertTrue(respaldar_db._es_respaldo_valido(ruta))

    def test_invalidos_y_temporales_ajenos_no_cuentan_ni_se_eliminan(self):
        self.directorio_respaldos.mkdir(exist_ok=True)
        vacio = self.directorio_respaldos / "proyecta_20200101_000000_vacio.db"
        temporal = self.directorio_respaldos / ".proyecta_ajeno.db.tmp"
        vacio.touch()
        temporal.write_bytes(b"ajeno")
        self._crear_respaldo_valido("proyecta_20200101_000001.db")
        self.assertEqual(respaldar_db.respaldar(mantener=1), 0)
        self.assertTrue(vacio.exists())
        self.assertEqual(temporal.read_bytes(), b"ajeno")
        self.assertEqual(len(respaldar_db._respaldos_validos(self.directorio_respaldos)), 1)

    def test_fallo_de_unlink_detiene_purga_y_reporta_retencion_incompleta(self):
        anteriores = [
            self._crear_respaldo_valido(f"proyecta_20200101_00000{i}.db")
            for i in range(3)
        ]
        unlink_real = Path.unlink

        def unlink_controlado(ruta, *args, **kwargs):
            if ruta == anteriores[0]:
                raise OSError("permiso")
            return unlink_real(ruta, *args, **kwargs)

        salida = io.StringIO()
        with mock.patch.object(Path, "unlink", unlink_controlado), redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(mantener=1), 0)
        self.assertIn("Respaldo correcto, etapa=purga retención incompleta", salida.getvalue())
        self.assertIn("etapa=purga", salida.getvalue())
        self.assertEqual(len(self._dbs()), 4)
        self.assertTrue(all(ruta.exists() for ruta in anteriores))


class PruebaSidecarsWAL(_BaseRespaldarRobusto):
    """respaldar() copia el ORIGEN con .backup(), que copia también el
    flag de journal_mode de su header -- si el origen real está en WAL
    (como db.py), el temporal (y luego el publicado) heredaban ese flag
    sin que nadie lo pidiera. Cualquier apertura posterior (integrity_check
    acá mismo, o la validación de retención sobre CUALQUIER respaldo
    histórico) le creaba -shm/-wal huérfanos, porque os.replace() solo
    renombra el archivo base. Estas pruebas inspeccionan el directorio con
    Path.iterdir() crudo a propósito -- _dbs()/_temporales() filtran por
    glob y son ciegos a -wal/-shm, que es justo lo que hay que ver acá."""

    def _activar_wal_en_origen(self):
        conexion = sqlite3.connect(self.ruta_db)
        conexion.execute("PRAGMA journal_mode=WAL")
        conexion.close()

    def _listado_crudo(self):
        self.directorio_respaldos.mkdir(exist_ok=True)
        return sorted(p.name for p in self.directorio_respaldos.iterdir())

    def _sidecars_en_listado(self):
        return [n for n in self._listado_crudo() if n.endswith("-wal") or n.endswith("-shm")]

    def test_respaldo_exitoso_no_deja_sidecars_wal_shm(self):
        self._activar_wal_en_origen()
        self.assertEqual(respaldar_db.respaldar(), 0)
        self.assertEqual(self._sidecars_en_listado(), [])

    def test_fallo_de_validacion_no_deja_sidecars(self):
        self._activar_wal_en_origen()
        inspeccion_real = respaldar_db._inspeccionar_sqlite

        def falla_temporal(ruta, huella_esperada=None):
            if str(ruta).endswith(".tmp"):
                raise ValueError("integridad simulada")
            return inspeccion_real(ruta, huella_esperada)

        with mock.patch.object(respaldar_db, "_inspeccionar_sqlite", side_effect=falla_temporal):
            self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertEqual(self._listado_crudo(), [])

    def _crear_temporal_con_sidecars_reales_y_fallar(self, temporal, error):
        Path(temporal).write_bytes(b"contenido base simulado")
        Path(f"{temporal}-wal").write_bytes(b"contenido wal simulado")
        Path(f"{temporal}-shm").write_bytes(b"x" * 32768)
        raise error

    def test_keyboard_interrupt_limpia_base_wal_y_shm_reales(self):
        self._activar_wal_en_origen()
        efecto = lambda conexion, temporal: self._crear_temporal_con_sidecars_reales_y_fallar(
            temporal, KeyboardInterrupt()
        )
        with mock.patch.object(respaldar_db, "_copiar_online", side_effect=efecto):
            with self.assertRaises(KeyboardInterrupt):
                respaldar_db.respaldar()
        self.assertEqual(self._listado_crudo(), [])

    def test_system_exit_limpia_base_wal_y_shm_reales(self):
        self._activar_wal_en_origen()
        efecto = lambda conexion, temporal: self._crear_temporal_con_sidecars_reales_y_fallar(
            temporal, SystemExit(9)
        )
        with mock.patch.object(respaldar_db, "_copiar_online", side_effect=efecto):
            with self.assertRaises(SystemExit):
                respaldar_db.respaldar()
        self.assertEqual(self._listado_crudo(), [])

    def test_respaldo_publicado_journal_mode_es_exactamente_delete(self):
        self._activar_wal_en_origen()
        self.assertEqual(respaldar_db.respaldar(), 0)
        publicado = self._dbs()[0]
        conexion = sqlite3.connect(str(publicado))
        modo = conexion.execute("PRAGMA journal_mode").fetchone()[0]
        conexion.close()
        self.assertEqual(modo, "delete")

    def test_fallo_normalizar_journal_mode_limpia_temporal(self):
        # Simula que PRAGMA journal_mode=DELETE "no pega" -- respaldar_db
        # debe verificar el resultado devuelto, no confiar ciegamente en
        # que el PRAGMA hizo lo que pidió. sqlite3.Connection es un tipo
        # inmutable (no se puede parchear su método directo en CPython
        # 3.14+) -- se intercepta con una subclase real vía el parámetro
        # `factory` de sqlite3.connect(), inyectada solo para la conexión
        # del temporal (nunca para el origen ni para otras rutas).
        self._activar_wal_en_origen()

        class _ConexionQueMiente(sqlite3.Connection):
            def execute(self, sql, *parametros):
                resultado = super().execute(sql, *parametros)
                if sql == "PRAGMA journal_mode=DELETE":
                    class _CursorFalso:
                        def fetchone(self_c):
                            return ("wal",)
                    return _CursorFalso()
                return resultado

        conectar_real = sqlite3.connect

        def connect_controlado(ruta, *args, **kwargs):
            if str(ruta).endswith(".db.tmp"):
                kwargs.setdefault("factory", _ConexionQueMiente)
            return conectar_real(ruta, *args, **kwargs)

        salida = io.StringIO()
        with mock.patch.object(respaldar_db.sqlite3, "connect", side_effect=connect_controlado), \
             redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertIn("etapa=copia", salida.getvalue())
        self.assertEqual(self._listado_crudo(), [])

    def test_fallo_real_antes_de_normalizar_limpia_temporal_y_sidecars(self):
        # Reforzada: acá el fallo no es un valor de retorno mentiroso --
        # es una excepción real lanzada mientras se intenta normalizar,
        # con el temporal ya en un estado parcial real (base + -wal +
        # -shm, como podría dejar un error de I/O genuino a mitad de la
        # transición de journal_mode). Confirma que los tres se limpian
        # y que no se publica ningún respaldo final.
        self._activar_wal_en_origen()
        conectar_real = sqlite3.connect

        def connect_controlado(ruta, *args, **kwargs):
            if str(ruta).endswith(".db.tmp"):
                ruta_str = str(ruta)

                class _ConexionQueFallaAlNormalizar(sqlite3.Connection):
                    def execute(self, sql, *parametros):
                        if sql == "PRAGMA journal_mode=DELETE":
                            Path(f"{ruta_str}-wal").write_bytes(
                                b"estado parcial de journal_mode"
                            )
                            Path(f"{ruta_str}-shm").write_bytes(b"x" * 32768)
                            raise sqlite3.OperationalError("fallo de I/O real simulado")
                        return super().execute(sql, *parametros)

                kwargs.setdefault("factory", _ConexionQueFallaAlNormalizar)
            return conectar_real(ruta, *args, **kwargs)

        salida = io.StringIO()
        with mock.patch.object(respaldar_db.sqlite3, "connect", side_effect=connect_controlado), \
             redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(), 1)
        self.assertIn("etapa=copia", salida.getvalue())
        self.assertEqual(self._listado_crudo(), [])

    def test_historico_con_wal_de_cero_bytes_se_valida_sin_crear_sidecars(self):
        # Header marcado WAL, pero el -wal presente pesa 0 bytes -- nada
        # pendiente, el archivo base es autónomo y se valida con
        # immutable=1 sin que aparezcan sidecars nuevos.
        historico = self._crear_respaldo_valido("proyecta_20200101_000000.db")
        conexion = sqlite3.connect(str(historico))
        conexion.execute("PRAGMA journal_mode=WAL")
        conexion.close()
        wal, shm = respaldar_db._sidecars_wal(historico)
        wal.write_bytes(b"")
        shm.unlink(missing_ok=True)

        antes = self._listado_crudo()
        self.assertTrue(respaldar_db._es_respaldo_valido(historico))
        despues = self._listado_crudo()

        self.assertEqual(antes, despues)
        self.assertEqual(wal.stat().st_size, 0)

    def test_historico_con_transaccion_solo_en_wal_no_es_valido_y_queda_intacto(self):
        # Una transacción confirmada que todavía no se plegó al archivo
        # base: -wal presente y > 0 bytes. immutable=1 leería el archivo
        # base solo, ignorando esa transacción -- inseguro. Se clasifica
        # como no autónomo, no cuenta para retención, y no se toca ni él
        # ni sus sidecars sin importar qué tan ajustado esté `mantener`.
        #
        # Cerrar la conexión escritora dispara un checkpoint automático
        # que pliega el WAL de vuelta al archivo base y lo vacía -- lo
        # opuesto a lo que este escenario necesita. La política que se
        # prueba acá es puramente de tamaño de archivo (ver
        # _es_respaldo_valido), no interpreta el contenido del WAL, así
        # que sintetizar bytes no vacíos en el -wal reproduce exactamente
        # la condición real sin pelear contra el checkpoint automático.
        historico = self._crear_respaldo_valido("proyecta_20200101_000000.db")
        wal, shm = respaldar_db._sidecars_wal(historico)
        wal.write_bytes(b"frames de una transaccion confirmada, sin plegar")
        shm.write_bytes(b"x" * 32768)

        self.assertGreater(wal.stat().st_size, 0)

        salida = io.StringIO()
        with redirect_stdout(salida):
            es_valido = respaldar_db._es_respaldo_valido(historico)
        self.assertFalse(es_valido)
        self.assertIn(historico.name, salida.getvalue())
        self.assertIn("WAL no vacío", salida.getvalue())

        db_antes = historico.read_bytes()
        wal_antes = wal.read_bytes()

        # mantener=1 con OTRO respaldo válido de por medio -- si el
        # histórico contara como válido, sería el primero en purgarse.
        self._crear_respaldo_valido("proyecta_20200101_000001.db")
        self.assertEqual(respaldar_db.respaldar(mantener=1), 0)

        self.assertTrue(historico.exists())
        self.assertTrue(wal.exists())
        self.assertTrue(shm.exists())
        self.assertEqual(historico.read_bytes(), db_antes)
        self.assertEqual(wal.read_bytes(), wal_antes)

    def test_fallo_al_borrar_base_igual_intenta_wal_y_shm(self):
        self._activar_wal_en_origen()
        unlink_real = Path.unlink

        def unlink_controlado(ruta, *args, **kwargs):
            if str(ruta).endswith(".db.tmp"):
                raise OSError("permiso denegado simulado en la base")
            return unlink_real(ruta, *args, **kwargs)

        efecto = lambda conexion, temporal: self._crear_temporal_con_sidecars_reales_y_fallar(
            temporal, sqlite3.OperationalError("disk full simulado")
        )
        with mock.patch.object(respaldar_db, "_copiar_online", side_effect=efecto), \
             mock.patch.object(Path, "unlink", unlink_controlado):
            self.assertEqual(respaldar_db.respaldar(), 1)

        restantes = self._listado_crudo()
        self.assertEqual(len(restantes), 1)
        self.assertTrue(restantes[0].endswith(".db.tmp"))

    def test_fallo_al_borrar_wal_igual_intenta_shm(self):
        self._activar_wal_en_origen()
        unlink_real = Path.unlink

        def unlink_controlado(ruta, *args, **kwargs):
            if str(ruta).endswith(".db.tmp-wal"):
                raise OSError("permiso denegado simulado en el wal")
            return unlink_real(ruta, *args, **kwargs)

        efecto = lambda conexion, temporal: self._crear_temporal_con_sidecars_reales_y_fallar(
            temporal, sqlite3.OperationalError("disk full simulado")
        )
        with mock.patch.object(respaldar_db, "_copiar_online", side_effect=efecto), \
             mock.patch.object(Path, "unlink", unlink_controlado):
            self.assertEqual(respaldar_db.respaldar(), 1)

        restantes = self._listado_crudo()
        self.assertEqual(len(restantes), 1)
        self.assertTrue(restantes[0].endswith(".db.tmp-wal"))

    def test_purga_nunca_toca_conjunto_con_wal_no_vacio(self):
        historico = self._crear_respaldo_valido("proyecta_20200101_000000.db")
        wal, shm = respaldar_db._sidecars_wal(historico)
        wal.write_bytes(b"frames de una transaccion confirmada, sin plegar")
        shm.write_bytes(b"x" * 32768)
        self.assertGreater(wal.stat().st_size, 0)

        # mantener=1: con el respaldo nuevo que se va a crear, cualquier
        # histórico "válido" quedaría sin cupo y se purgaría.
        self.assertEqual(respaldar_db.respaldar(mantener=1), 0)

        self.assertTrue(historico.exists())
        self.assertTrue(wal.exists())
        self.assertTrue(shm.exists())

    def test_purgar_respaldo_elimina_sus_sidecars_asociados(self):
        viejo = self._crear_respaldo_valido("proyecta_20200101_000000.db")
        # sidecars "heredados" (de antes de este fix) del respaldo que se
        # va a purgar -- deben desaparecer junto con el .db.
        Path(f"{viejo}-wal").write_bytes(b"")
        Path(f"{viejo}-shm").write_bytes(b"x" * 32768)
        self._crear_respaldo_valido("proyecta_20200101_000001.db")

        self.assertEqual(respaldar_db.respaldar(mantener=1), 0)

        self.assertFalse(viejo.exists())
        self.assertFalse(Path(f"{viejo}-wal").exists())
        self.assertFalse(Path(f"{viejo}-shm").exists())

    def test_fallo_al_borrar_base_durante_purga_igual_intenta_wal_y_shm(self):
        viejo = self._crear_respaldo_valido("proyecta_20200101_000000.db")
        Path(f"{viejo}-wal").write_bytes(b"")
        Path(f"{viejo}-shm").write_bytes(b"x" * 32768)
        unlink_real = Path.unlink

        def unlink_controlado(ruta, *args, **kwargs):
            if ruta == viejo:
                raise OSError("permiso denegado simulado en la base de purga")
            return unlink_real(ruta, *args, **kwargs)

        salida = io.StringIO()
        with mock.patch.object(Path, "unlink", unlink_controlado), redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(mantener=1), 0)

        self.assertIn("Respaldo correcto, etapa=purga retención incompleta", salida.getvalue())
        self.assertTrue(viejo.exists())  # falló borrar la base -- sigue ahí
        self.assertFalse(Path(f"{viejo}-wal").exists())  # pero igual se intentó el wal
        self.assertFalse(Path(f"{viejo}-shm").exists())  # y el shm

    def test_fallo_al_borrar_wal_durante_purga_igual_intenta_shm(self):
        viejo = self._crear_respaldo_valido("proyecta_20200101_000000.db")
        Path(f"{viejo}-wal").write_bytes(b"")
        Path(f"{viejo}-shm").write_bytes(b"x" * 32768)
        ruta_wal = f"{viejo}-wal"
        unlink_real = Path.unlink

        def unlink_controlado(ruta, *args, **kwargs):
            if str(ruta) == ruta_wal:
                raise OSError("permiso denegado simulado en el wal de purga")
            return unlink_real(ruta, *args, **kwargs)

        salida = io.StringIO()
        with mock.patch.object(Path, "unlink", unlink_controlado), redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(mantener=1), 0)

        self.assertIn("Respaldo correcto, etapa=purga retención incompleta", salida.getvalue())
        self.assertFalse(viejo.exists())  # la base sí se pudo borrar
        self.assertTrue(Path(ruta_wal).exists())  # el wal falló -- sigue ahí
        self.assertFalse(Path(f"{viejo}-shm").exists())  # pero igual se intentó el shm

    def test_tras_fallo_de_purga_no_purga_otro_respaldo(self):
        primero = self._crear_respaldo_valido("proyecta_20200101_000000.db")
        segundo = self._crear_respaldo_valido("proyecta_20200101_000001.db")
        unlink_real = Path.unlink

        def unlink_controlado(ruta, *args, **kwargs):
            if ruta == primero:
                raise OSError("permiso denegado simulado")
            return unlink_real(ruta, *args, **kwargs)

        salida = io.StringIO()
        with mock.patch.object(Path, "unlink", unlink_controlado), redirect_stdout(salida):
            self.assertEqual(respaldar_db.respaldar(mantener=1), 0)

        self.assertIn("Respaldo correcto, etapa=purga retención incompleta", salida.getvalue())
        self.assertTrue(primero.exists())  # falló, sigue ahí
        self.assertTrue(segundo.exists())  # ni siquiera se intentó -- la purga se detuvo

    def test_purga_no_toca_sidecars_ni_temporales_de_otros_archivos(self):
        self._crear_respaldo_valido("proyecta_20200101_000001.db")
        ajeno_wal = self.directorio_respaldos / "algo_no_relacionado.db-wal"
        ajeno_shm = self.directorio_respaldos / "algo_no_relacionado.db-shm"
        ajeno_wal.write_bytes(b"dato")
        ajeno_shm.write_bytes(b"dato")
        temporal_ajeno = self.directorio_respaldos / ".otro_proceso.db.tmp"
        temporal_ajeno.write_bytes(b"dato")
        self._crear_respaldo_valido("proyecta_20200101_000000.db")

        self.assertEqual(respaldar_db.respaldar(mantener=2), 0)

        self.assertTrue(ajeno_wal.exists())
        self.assertTrue(ajeno_shm.exists())
        self.assertTrue(temporal_ajeno.exists())


class PruebaObservabilidadScheduler(unittest.TestCase):
    def _ejecutar_una_iteracion(self, resultado):
        import api.main as api_main

        with mock.patch.object(api_main, "_aplicar_migraciones_pendientes"), \
             mock.patch.object(api_main, "_respaldar_db", return_value=resultado), \
             mock.patch.object(api_main.time, "sleep", side_effect=SystemExit), \
             mock.patch.object(api_main, "_logger") as logger:
            with self.assertRaises(SystemExit):
                api_main._bucle_arranque_en_segundo_plano()
        return logger

    def test_scheduler_registra_lock_ocupado_como_omitido(self):
        logger = self._ejecutar_una_iteracion(respaldar_db.CODIGO_LOCK_OCUPADO)
        logger.warning.assert_called_once()
        self.assertIn("estado=omitido", logger.warning.call_args.args[0])
        logger.error.assert_not_called()

    def test_scheduler_registra_retorno_fallido(self):
        logger = self._ejecutar_una_iteracion(1)
        logger.error.assert_called_once()
        self.assertIn("estado=fallido", logger.error.call_args.args[0])
        logger.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
