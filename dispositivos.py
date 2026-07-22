"""Deteccion de capturadoras USB via DirectShow."""

import re
import subprocess

_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

# Dispositivos virtuales que no son capturadoras reales.
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

    nombres = []
    for linea in salida.splitlines():
        if m := re.search(r'"([^"]+)"\s*\(video\)', linea):
            nombres.append(m.group(1))
    return nombres


def son_virtuales(nombres: list[str]) -> bool:
    """True si todo lo detectado son webcams virtuales (no hay hardware real)."""
    return bool(nombres) and all(
        any(v in n.lower() for v in _VIRTUALES) for n in nombres
    )


def formatos(dispositivo: str) -> str:
    """Formatos que soporta una capturadora, para diagnostico."""
    try:
        return subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "dshow", "-list_options", "true",
             "-i", f"video={dispositivo}"],
            capture_output=True, text=True, errors="replace", timeout=15,
            creationflags=_SIN_VENTANA,
        ).stderr
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
