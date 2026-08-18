"""Medición de memoria del worker de planos para correr en un entorno
Linux real (Render Shell, u otro Linux equivalente) -- NO requiere
psutil ni ninguna dependencia nueva, ni polling desde afuera del
proceso: usa /proc/self/status, que es el propio kernel llevando la
cuenta del pico histórico (VmPeak = pico de memoria virtual, VmHWM =
pico de RSS -- "high water mark") -- exacto, sin ventana de carrera,
a diferencia del muestreo por `ps` usado en la medición de macOS
(medir_rss_vms_macos.py), que sí tiene esa limitación.

Instrumentación temporal -- pensada para copiarse a mano a una sesión
de Render Shell (o cualquier caja Linux con el mismo entorno de
proyecta-data), correrse UNA vez por cada PDF de referencia, y
borrarse. No se importa desde ningún módulo de la app ni se referencia
en ningún router -- no queda ningún rastro en el código desplegado
salvo mientras alguien lo pega y ejecuta a mano.

USO (ver instrucciones completas en el mensaje que acompaña este
archivo):
    PYTHONPATH=. python3 medir_memoria_linux.py /ruta/al/plano.pdf
"""
import subprocess
import sys
import time


def _leer_proc_status(pid):
    """VmPeak/VmHWM en KB, directo del kernel -- None si no están (no-Linux)."""
    try:
        with open(f"/proc/{pid}/status") as f:
            texto = f.read()
    except FileNotFoundError:
        return None
    datos = {}
    for linea in texto.splitlines():
        if linea.startswith("VmPeak:"):
            datos["vm_peak_kb"] = int(linea.split()[1])
        elif linea.startswith("VmHWM:"):
            datos["vm_hwm_kb"] = int(linea.split()[1])
    return datos or None


def medir(ruta_pdf):
    """Corre _procesar_plano_pdf en ESTE proceso (no en un hijo nuevo) para
    poder leer su propio /proc/self/status al final -- más simple que
    coordinar con un hijo, y el número que importa es el mismo: el pico
    de memoria de ejecutar exactamente esa función."""
    t0 = time.perf_counter()
    from api.repositorio_proyectos import _procesar_plano_pdf
    try:
        analisis = _procesar_plano_pdf(ruta_pdf)
        laminas = analisis.get("cantidad_laminas")
        resultado = "exito"
    except Exception as e:
        laminas = None
        resultado = f"fallo:{type(e).__name__}"
    duracion = time.perf_counter() - t0

    pico = _leer_proc_status("self")
    if pico is None:
        print("ADVERTENCIA: /proc/self/status no disponible -- esto no es Linux, "
              "los números de este script no son válidos acá.", file=sys.stderr)
        return

    print(f"PDF={ruta_pdf}")
    print(f"resultado={resultado} laminas={laminas} duracion_s={duracion:.2f}")
    print(f"VmPeak (pico memoria VIRTUAL): {pico['vm_peak_kb']/1024:.1f} MB")
    print(f"VmHWM  (pico memoria RSS):     {pico['vm_hwm_kb']/1024:.1f} MB")


def medir_baseline_proceso_actual():
    """Sin tocar ningún PDF -- solo el costo de arrancar Python + importar
    lo que analizar_plano importa a nivel de módulo (sin lectura_planos,
    que se importa diferido dentro de _procesar_plano_pdf a propósito --
    ver el comentario en api/repositorio_proyectos.py)."""
    import api.repositorio_proyectos  # noqa: F401 -- fuerza los imports de nivel de módulo
    pico = _leer_proc_status("self")
    if pico:
        print(f"BASELINE (solo imports de módulo, sin ningún PDF): "
              f"VmPeak={pico['vm_peak_kb']/1024:.1f}MB VmHWM={pico['vm_hwm_kb']/1024:.1f}MB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: PYTHONPATH=. python3 medir_memoria_linux.py /ruta/al/plano.pdf", file=sys.stderr)
        sys.exit(1)
    medir_baseline_proceso_actual()
    medir(sys.argv[1])
