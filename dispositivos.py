"""Deteccion de dispositivos DirectShow (video y audio) via FFmpeg.

Se pregunta al propio FFmpeg en vez de consultar Windows: asi los nombres
obtenidos son exactamente los que FFmpeg espera despues en '-i video=...' o
'-i audio=...'. Los que da el Administrador de dispositivos no siempre coinciden.

Se devuelve TODO lo detectado, sin filtrar. El operador elige en la interfaz
que dispositivo va en cada camara, asi que una webcam virtual (un movil via
Iriun, por ejemplo) es una fuente valida si asi lo decide.
"""

import re
import subprocess

_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

# Cada dispositivo aparece en una linea '"Nombre" (video)' o '"Nombre" (audio)'.
# Se ignoran las lineas "Alternative name", que traen el identificador largo.
_RE_VIDEO = re.compile(r'"([^"]+)"\s*\(video\)')
_RE_AUDIO = re.compile(r'"([^"]+)"\s*\(audio\)')


def _enumerar() -> str:
    """Salida cruda de FFmpeg con la lista de dispositivos DirectShow.

    FFmpeg la emite por stderr y con codigo de salida 1; es normal. Devuelve
    cadena vacia si FFmpeg no esta o no responde.
    """
    try:
        return subprocess.run(
            ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, text=True, errors="replace", timeout=15,
            creationflags=_SIN_VENTANA,
        ).stderr
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _extraer(patron: re.Pattern, salida: str) -> list[str]:
    """Nombres que casan un patron, sin duplicados y en orden de aparicion."""
    nombres: list[str] = []
    for linea in salida.splitlines():
        if (m := patron.search(linea)) and m.group(1) not in nombres:
            nombres.append(m.group(1))
    return nombres


def listar_dispositivos() -> list[str]:
    """Nombres DirectShow de los dispositivos de video conectados."""
    return _extraer(_RE_VIDEO, _enumerar())


def listar_audio() -> list[str]:
    """Nombres DirectShow de los dispositivos de audio (microfonos) conectados."""
    return _extraer(_RE_AUDIO, _enumerar())


def listar_video_y_audio() -> tuple[list[str], list[str]]:
    """Video y audio en una sola llamada a FFmpeg (evita enumerar dos veces)."""
    salida = _enumerar()
    return _extraer(_RE_VIDEO, salida), _extraer(_RE_AUDIO, salida)


def emparejar_audio(video: str, audios: list[str]) -> str | None:
    """Micro que corresponde a una capturadora de video, o None si no hay.

    Las capturadoras exponen su micro con el nombre del video entre parentesis:
    'USB Video #2' -> 'Microphone (USB Video #2)'. Se empareja por ese sufijo
    exacto '(<video>)', no por 'contiene el nombre': con varias capturadoras
    identicas ('USB Video', 'USB Video #2', ...) un 'contiene' cogeria el micro
    equivocado, porque 'USB Video' es subcadena de 'USB Video #2'.
    """
    sufijo = f"({video})"
    for a in audios:
        if a.endswith(sufijo):
            return a
    return None


def previsualizar(dispositivo: str, resolucion: str, fps: int) -> subprocess.Popen | None:
    """Abre una ventana de ffplay con la imagen en vivo del dispositivo.

    Devuelve el proceso para poder cerrarlo despues, o None si ffplay no esta.
    No sirve para el dispositivo que se esta grabando: DirectShow no deja que
    dos procesos abran la misma capturadora a la vez.

    -fflags nobuffer y -flags low_delay bajan la latencia; para comprobar el
    encuadre interesa ver "ahora", no una imagen con retardo.

    -x/-y fijan el tamano de la ventana. Sin ellos, ffplay abre a la resolucion
    del video (p.ej. 1920x1080), que tapa la pantalla. 640x360 mantiene el 16:9
    y deja ver la aplicacion al lado.
    """
    cmd = [
        "ffplay", "-hide_banner", "-loglevel", "error",
        "-f", "dshow",
        "-video_size", resolucion,
        "-framerate", str(fps),
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-x", "640", "-y", "360",
        "-window_title", f"Previsualizacion - {dispositivo}",
        "-i", f"video={dispositivo}",
    ]
    try:
        return subprocess.Popen(cmd, creationflags=_SIN_VENTANA)
    except FileNotFoundError:
        return None


def formatos(dispositivo: str) -> str:
    """Formatos y resoluciones que admite un dispositivo, en crudo.

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
