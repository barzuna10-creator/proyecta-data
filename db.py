import os
import sqlite3

# PRODUCTION_READINESS_REVIEW.md, hallazgo A7/H1: antes hardcodeado sin
# ninguna forma de apuntar un despliegue distinto (staging, un disco
# persistente en otra ruta) a otra base de datos sin editar código. El
# valor por defecto es el mismo de siempre -- esto no cambia ningún
# comportamiento existente si DATABASE_PATH no está seteada.
BASE_DATOS = os.environ.get("DATABASE_PATH", "database/proyecta.db")


def conectar():
    conexion = sqlite3.connect(BASE_DATOS, timeout=10)
    conexion.execute("PRAGMA foreign_keys = ON")
    # WAL: los lectores (API) no quedan bloqueados mientras un crawler
    # mantiene una transacción de escritura larga abierta (guardar miles
    # de productos). busy_timeout de cortesía además del timeout del
    # driver, para esperar en vez de fallar de inmediato ante un choque
    # puntual de escritura. Se puede correr muchas veces sin problema --
    # journal_mode=WAL queda grabado en el archivo, PRAGMA es idempotente.
    conexion.execute("PRAGMA journal_mode = WAL")
    conexion.execute("PRAGMA busy_timeout = 10000")
    conexion.row_factory = sqlite3.Row
    return conexion
