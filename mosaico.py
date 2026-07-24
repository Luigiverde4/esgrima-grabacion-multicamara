"""Genera un mosaico 1920x1080 con los tres POVs de un asalto.

Disposicion: la camara frontal grande arriba (centrada), y las dos laterales
partiendose la mitad inferior. El audio se toma de la frontal.

Se ejecuta en un hilo aparte (como la subida): recodificar tres 1080p tarda mas
que el propio asalto, y bloquear la interfaz entre asaltos seria inaceptable en
directo. Informa por callbacks.
"""

import subprocess
import threading
from pathlib import Path
from typing import Callable

_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

# Lienzo y geometria. El frontal ocupa la franja superior (720 de alto), los
# laterales se reparten la inferior (360 de alto) a partes iguales.
_ANCHO, _ALTO = 1920, 1080
_ALTO_SUP = 720
_ALTO_INF = _ALTO - _ALTO_SUP          # 360


def _celda(idx: int, ancho: int, alto: int, etiqueta: str) -> str:
    """Escala una entrada a una celda 'ancho x alto' SIN deformar.

    force_original_aspect_ratio=decrease conserva la proporcion (una cam 16:9
    nunca se estira); el pad rellena con negro hasta el tamano exacto de la
    celda y centra la imagen. setsar=1 evita que overlay descoloque nada.
    """
    return (
        f"[{idx}:v]scale={ancho}:{alto}:force_original_aspect_ratio=decrease,"
        f"pad={ancho}:{alto}:(ow-iw)/2:(oh-ih)/2,setsar=1[{etiqueta}];"
    )


def _filtro(frontal_idx: int, izq_idx: int, der_idx: int) -> str:
    """Cadena filter_complex para el mosaico.

    Los indices son la posicion de cada entrada -i (0,1,2). Cada POV se ajusta a
    su celda respetando la proporcion 16:9 (letterbox en negro si hace falta),
    asi ninguna imagen se estira. El frontal (1280x720) se centra arriba; los
    laterales (960x360) llenan cada mitad inferior.
    """
    x_frontal = (_ANCHO - 1280) // 2   # centra el frontal: 320
    return (
        _celda(frontal_idx, 1280, _ALTO_SUP, "top")
        + _celda(izq_idx, 960, _ALTO_INF, "bl")
        + _celda(der_idx, 960, _ALTO_INF, "br")
        + f"color=c=black:s={_ANCHO}x{_ALTO}[bg];"
        + f"[bg][top]overlay=x={x_frontal}:y=0[a];"
        + f"[a][bl]overlay=x=0:y={_ALTO_SUP}[b];"
        + f"[b][br]overlay=x=960:y={_ALTO_SUP}"
    )


def generar(carpeta: Path, frontal: str, izquierda: str, derecha: str,
            al_terminar: Callable[[bool, str], None],
            audio_de: str | None = None) -> threading.Thread | None:
    """Crea 'mosaico.mkv' en 'carpeta' a partir de los tres MKV de camara.

    frontal/izquierda/derecha son nombres de fichero (p.ej. 'cam2.mkv'). El
    audio se toma de 'audio_de' (por defecto, el frontal). al_terminar(ok, msg)
    se llama una vez desde el hilo.

    Devuelve el hilo, o None si falta algun fichero de entrada (no se genera).
    """
    entradas = [carpeta / frontal, carpeta / izquierda, carpeta / derecha]
    if not all(f.exists() and f.stat().st_size > 0 for f in entradas):
        return None

    destino = carpeta / "mosaico.mkv"
    audio_de = audio_de or frontal
    # Indice de la entrada de la que sale el audio, para el mapeo.
    orden = [frontal, izquierda, derecha]
    idx_audio = orden.index(audio_de) if audio_de in orden else 0

    def tarea() -> None:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(entradas[0]), "-i", str(entradas[1]), "-i", str(entradas[2]),
            "-filter_complex", _filtro(0, 1, 2),
            # El video sale del filtro; el audio, directo de una camara.
            "-map", f"{idx_audio}:a?",     # '?' -> no falla si esa cam no tiene audio
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k",
            str(destino),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  errors="replace", creationflags=_SIN_VENTANA)
        except FileNotFoundError:
            al_terminar(False, "FFmpeg no encontrado en el PATH")
            return
        # A veces el mosaico se crea aunque ffmpeg devuelva no-cero (p.ej. si un
        # proceso externo lo interrumpe tras cerrar el fichero). Se considera OK
        # si el fichero existe y tiene contenido; si no, se informa del motivo.
        if destino.exists() and destino.stat().st_size > 0:
            al_terminar(True, f"Mosaico generado: {destino.name}")
        else:
            detalle = proc.stderr.strip()[-150:] or f"codigo {proc.returncode}"
            al_terminar(False, f"Error en el mosaico: {detalle}")

    hilo = threading.Thread(target=tarea, daemon=True)
    hilo.start()
    return hilo
