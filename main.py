from crawlers import brenes, carbone, ellagar, epa

PROVEEDORES = [ellagar, epa, brenes, carbone]


def main():
    print("=== PROYECTA CR ===")

    for proveedor in PROVEEDORES:
        try:
            proveedor.actualizar()
        except Exception as error:
            print(f"❌ Error actualizando {proveedor.__name__}: {error}")


if __name__ == "__main__":
    main()
