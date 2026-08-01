import sqlite3

BASE_DATOS = "database/proyecta.db"


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
