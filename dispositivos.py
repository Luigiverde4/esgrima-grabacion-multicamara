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
from dataclasses import dataclass

from ffmpeg_utils import SIN_VENTANA as _SIN_VENTANA

# Cada dispositivo son dos lineas: '"Nombre" (video)' y, debajo, su
# 'Alternative name "<id>"'. El id es el identificador unico de FFmpeg (para las
# capturadoras, el path USB fisico); es lo que distingue dos capturadoras con el
# mismo nombre visible.
_RE_DISPOSITIVO = re.compile(r'"([^"]+)"\s*\((video|audio)\)')
_RE_ALT = re.compile(r'Alternative name "([^"]+)"')


@dataclass(frozen=True)
class Dispositivo:
    """Un dispositivo DirectShow: nombre visible + id de hardware.

    'id' es lo que se usa para grabar y lo que se guarda: unico aunque el nombre
    se repita. 'nombre' es solo para mostrar al operador.
    """
    nombre: str
    id: str

    def etiqueta(self, indice: int | None = None) -> str:
        """Texto para el desplegable. 'indice' desambigua nombres repetidos."""
        return self.nombre if indice is None else f"{self.nombre} ({indice})"


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


def _extraer(tipo: str, salida: str) -> list[Dispositivo]:
    """Dispositivos de un tipo ('video'/'audio') con su id de hardware.

    Se empareja cada linea de dispositivo con su 'Alternative name' inmediato.
    Si no hubiera alt name, el id cae al propio nombre. Se deduplica por id (no
    por nombre): asi dos capturadoras con el mismo nombre no se colapsan en una.
    """
    lineas = salida.splitlines()
    vistos: set[str] = set()
    dispositivos: list[Dispositivo] = []
    for i, linea in enumerate(lineas):
        m = _RE_DISPOSITIVO.search(linea)
        if not m or m.group(2) != tipo:
            continue
        nombre = m.group(1)
        # El alt name suele estar en la linea siguiente.
        ident = nombre
        if i + 1 < len(lineas) and (a := _RE_ALT.search(lineas[i + 1])):
            ident = a.group(1)
        if ident not in vistos:
            vistos.add(ident)
            dispositivos.append(Dispositivo(nombre, ident))
    return dispositivos


def listar_audio() -> list[Dispositivo]:
    """Dispositivos de audio (microfonos) conectados, con su id de hardware."""
    return _extraer("audio", _enumerar())


def listar_video_y_audio() -> tuple[list[Dispositivo], list[Dispositivo]]:
    """Video y audio en una sola llamada a FFmpeg (evita enumerar dos veces)."""
    salida = _enumerar()
    return _extraer("video", salida), _extraer("audio", salida)


def _raiz(nombre: str) -> str:
    """Nucleo comparable de un nombre de dispositivo.

    Quita el envoltorio del audio ('Microphone (...)', 'Digital Audio
    Interface (2- ...)'), prefijos de instancia tipo '2- ', las palabras
    'video'/'audio', y signos. Asi 'USB3.0 Video' y
    'Digital Audio Interface (2- USB3.0 Audio)' colapsan ambos a 'usb30'.
    """
    # Si hay parentesis, el nucleo del dispositivo esta dentro (asi es como
    # Windows nombra el micro de una capturadora).
    if m := re.search(r"\(([^()]+)\)", nombre):
        nombre = m.group(1)
    nombre = nombre.lower()
    nombre = re.sub(r"^\d+-\s*", "", nombre)          # prefijo de instancia '2- '
    nombre = re.sub(r"\b(video|audio)\b", "", nombre)  # la palabra video/audio
    nombre = re.sub(r"[^a-z0-9]", "", nombre)          # signos y espacios
    return nombre


def emparejar_audio(video: Dispositivo, audios: list[Dispositivo]) -> Dispositivo | None:
    """Micro que corresponde a una capturadora de video, o None si no hay.

    Se empareja por NOMBRE, no por id de hardware: los dispositivos de audio no
    exponen el path USB de su capturadora (usan '@device_cm_...\\wave_{GUID}'),
    asi que el sistema no da la relacion fisica video<->audio. El nombre es la
    unica pista. Dos intentos, del mas fiable al mas flexible:

    1. Sufijo exacto '(<nombre de video>)'. Es como nombran el micro las webcams
       tipo Iriun ('Iriun Webcam #2' -> 'Microphone (Iriun Webcam #2)').

    2. Raiz comun (ver _raiz): 'USB3.0 Video' <-> 'Digital Audio Interface
       (2- USB3.0 Audio)'. Si la raiz casa con EXACTAMENTE un micro se devuelve;
       si hay varios candidatos (dos capturadoras identicas, ambos micros con la
       misma raiz) se devuelve None: mejor que el operador elija a mano que
       arriesgar un cruce.
    """
    sufijo = f"({video.nombre})"
    for a in audios:
        if a.nombre.endswith(sufijo):
            return a

    raiz_video = _raiz(video.nombre)
    if not raiz_video:
        return None
    candidatos = [a for a in audios if _raiz(a.nombre) == raiz_video]
    return candidatos[0] if len(candidatos) == 1 else None


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
