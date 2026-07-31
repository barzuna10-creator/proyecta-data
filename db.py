import sqlite3

BASE_DATOS = "database/proyecta.db"


def conectar():
    conexion = sqlite3.connect(BASE_DATOS)
    conexion.execute("PRAGMA foreign_keys = ON")
    conexion.row_factory = sqlite3.Row
    return conexion
