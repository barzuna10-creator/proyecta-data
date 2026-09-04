"""Smoke check post-despliegue (ver Mission #003, Production Health &
Deploy Guard) -- corre a mano contra la URL pública real después de un
deploy, para confirmar que Zentra está listo para usuarios de verdad, no
solo que el proceso responde.

No es parte de ningún pipeline automático todavía -- eso necesita su
propia autorización explícita, por tocar .github/workflows/ (protegido
por AGENTS.md) o configuración de deploy hooks fuera de este repo. Este
script es el equivalente, para un humano, de correrlo a mano.

Falla cerrado (código de salida != 0) ante CUALQUIER cosa que no sea un
"listo" confirmado: timeout, error de conexión, status HTTP fuera de
2xx, JSON malformado o con forma inesperada, o /health reportando algo
distinto de status="ok" -- nunca asume éxito por default.

Uso:
    python3 herramientas_desarrollo/verificar_despliegue.py https://proyecta-data.onrender.com
"""

import sys

import requests

TIMEOUT_SEGUNDOS = 10


def _pedir_json(url):
    """Devuelve (cuerpo, None) si todo salió bien, o (None, motivo) ante
    cualquier fallo -- nunca lanza, nunca asume que un cuerpo parcial o
    con forma rara es aceptable."""
    try:
        respuesta = requests.get(url, timeout=TIMEOUT_SEGUNDOS)
    except requests.exceptions.Timeout:
        return None, f"timeout después de {TIMEOUT_SEGUNDOS}s"
    except requests.exceptions.RequestException as error:
        return None, f"error de conexión ({type(error).__name__})"

    if not (200 <= respuesta.status_code < 300):
        return None, f"HTTP {respuesta.status_code}"

    try:
        cuerpo = respuesta.json()
    except ValueError:
        return None, "el cuerpo de la respuesta no es JSON válido"

    if not isinstance(cuerpo, dict):
        return None, f"se esperaba un objeto JSON, llegó {type(cuerpo).__name__}"

    return cuerpo, None


def _verificar_salud(base_url):
    cuerpo, motivo = _pedir_json(f"{base_url}/health")
    if motivo:
        print(f"❌ /health: {motivo}")
        return False

    estado = cuerpo.get("status")
    if estado != "ok":
        print(f"❌ /health: status={estado!r} (se esperaba 'ok') -- checks={cuerpo.get('checks')}")
        return False

    print(f"✅ /health: status=ok checks={cuerpo.get('checks')}")
    return True


def _verificar_version(base_url):
    cuerpo, motivo = _pedir_json(f"{base_url}/version")
    if motivo:
        print(f"❌ /version: {motivo}")
        return False

    if "commit" not in cuerpo or "version" not in cuerpo:
        print(f"❌ /version: respuesta con forma inesperada (faltan campos): {cuerpo}")
        return False

    print(f"✅ /version: commit={cuerpo.get('commit')} version={cuerpo.get('version')}")
    return True


def verificar_despliegue(base_url):
    """True solo si AMBOS chequeos pasaron -- nunca "mejor que nada"."""
    base_url = base_url.rstrip("/")
    print(f"Verificando despliegue en {base_url}...")

    salud_ok = _verificar_salud(base_url)
    version_ok = _verificar_version(base_url)

    return salud_ok and version_ok


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python3 herramientas_desarrollo/verificar_despliegue.py <URL base>", file=sys.stderr)
        sys.exit(1)

    if verificar_despliegue(sys.argv[1]):
        print("✅ Despliegue verificado: listo para usuarios.")
        sys.exit(0)

    print("❌ Despliegue NO verificado -- ver detalle arriba.")
    sys.exit(1)
