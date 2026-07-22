"""Deteccion de capturadoras USB via DirectShow.

Se pregunta al propio FFmpeg en vez de consultar Windows: asi los nombres
obtenidos son exactamente los que FFmpeg espera despues en '-i video=...'.
Los que da el Administrador de dispositivos no siempre coinciden.
"""

import re
import subprocess

_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

# Dispositivos virtuales que no son capturadoras reales. Aparecen igual que una
# camara fisica, asi que sin este filtro se asignarian como si fueran validos.
_VIRTUALES = ("iriun", "obs virtual", "droidcam", "epoccam", "ndi", "xsplit")


def listar_camaras() -> list[str]:
    """Nombres DirectShow de las camaras de video conectadas.

    FFmpeg devuelve la lista por stderr y con codigo de salida 1; es normal.
    """
    try:
        salida = subprocess.run(
            ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, text=True, errors="replace", timeout=15,
            creationflags=_SIN_VENTANA,
        ).stderr
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    # Solo interesan las lineas '"Nombre" (video)'. Se descartan las de audio
    # y las de "Alternative name", que traen el identificador largo del sistema.
    nombres = []
    for linea in salida.splitlines():
        if m := re.search(r'"([^"]+)"\s*\(video\)', linea):
            nombres.append(m.group(1))
    return nombres


def son_virtuales(nombres: list[str]) -> bool:
    """True si TODO lo detectado son webcams virtuales.

    Se exige que lo sean todas: con una capturadora real entre ellas, la
    deteccion sigue siendo util y no conviene interrumpir al operador.
    """
    return bool(nombres) and all(
        any(v in n.lower() for v in _VIRTUALES) for n in nombres
    )


def formatos(dispositivo: str) -> str:
    """Formatos y resoluciones que admite una capturadora, en crudo.

    Utilidad de diagnostico, sin uso en la interfaz. Sirve para comprobar si
    una capturadora ofrece MJPEG (comprimido, tres caben en USB) o solo YUY2
    (sin comprimir, satura el bus con mas de una a 1080p).
    """
    try:
        return subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "dshow", "-list_options", "true",
             "-i", f"video={dispositivo}"],
            capture_output=True, text=True, errors="replace", timeout=15,
            creationflags=_SIN_VENTANA,
        ).stderr
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
