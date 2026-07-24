"""Genera un mosaico 1920x1080 con los tres POVs de un asalto.

Disposicion: la camara frontal grande arriba (centrada), y las dos laterales
partiendose la mitad inferior. El audio se toma de la frontal.

El mosaico se genera en un PROCESO INDEPENDIENTE con su propia ventana de
consola, no en un hilo de la app. Motivos:

  - Recodificar tres 1080p tarda MAS que el propio asalto. En directo no puede
    bloquear la interfaz entre asaltos.
  - Al ser un proceso propio (no un hilo daemon), sobrevive al cierre de la app:
    si el operador cierra la aplicacion con un mosaico a medias, el mosaico
    termina igualmente en su ventana.
  - La ventana muestra el progreso de FFmpeg y se cierra sola al terminar.

SEGURIDAD DEL FICHERO
    FFmpeg escribe en 'mosaico.parcial.mkv' y solo al terminar bien se renombra
    a 'mosaico.mkv'. Asi una interrupcion (cerrar la ventana, corte de luz)
    nunca deja un 'mosaico.mkv' corrupto que parezca valido. Es la misma logica
    de "no dar por bueno lo que no esta cerrado" que la grabacion.

BUGS DEL FILTRO QUE HABIA QUE EVITAR (ver historial):
  - 'color=black' es una fuente INFINITA. Sin 'shortest=1' en el primer overlay,
    el mosaico salia sin duracion (ffprobe: N/A) y podia colgarse.
  - Sin 'fps=<fps>' al final, FFmpeg inventaba 25 fps aunque las entradas fueran
    a 30. Se fuerza el fps real (viene de config) en la salida del filtro.
"""

import subprocess
from pathlib import Path

# Consola propia y visible para el mosaico. En Windows, CREATE_NEW_CONSOLE abre
# una ventana donde FFmpeg pinta su progreso; el operador ve como avanza y se
# cierra sola al terminar. Fuera de Windows (desarrollo) queda a 0 y hereda la
# consola actual.
_NUEVA_CONSOLA = subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, "CREATE_NEW_CONSOLE") else 0

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


def _filtro(frontal_idx: int, izq_idx: int, der_idx: int, fps: int) -> str:
    """Cadena filter_complex para el mosaico.

    Los indices son la posicion de cada entrada -i (0,1,2). Cada POV se ajusta a
    su celda respetando la proporcion 16:9 (letterbox en negro si hace falta),
    asi ninguna imagen se estira. El frontal (1280x720) se centra arriba; los
    laterales (960x360) llenan cada mitad inferior.

    Dos detalles imprescindibles (ver bugs en el docstring del modulo):
      - 'shortest=1' en el primer overlay: el fondo 'color' es infinito y sin
        esto el fichero sale sin duracion. Se corta a la entrada de video.
      - 'fps={fps}' al final: fija el framerate real; sin el, FFmpeg pone 25.
    """
    x_frontal = (_ANCHO - 1280) // 2   # centra el frontal: 320
    return (
        _celda(frontal_idx, 1280, _ALTO_SUP, "top")
        + _celda(izq_idx, 960, _ALTO_INF, "bl")
        + _celda(der_idx, 960, _ALTO_INF, "br")
        + f"color=c=black:s={_ANCHO}x{_ALTO}:r={fps}[bg];"
        + f"[bg][top]overlay=x={x_frontal}:y=0:shortest=1[a];"
        + f"[a][bl]overlay=x=0:y={_ALTO_SUP}[b];"
        + f"[b][br]overlay=x=960:y={_ALTO_SUP},fps={fps}[out]"
    )


def generar(carpeta: Path, frontal: str, izquierda: str, derecha: str,
            fps: int = 30, audio_de: str | None = None) -> subprocess.Popen | None:
    """Lanza la generacion de 'mosaico.mkv' en un proceso con ventana propia.

    frontal/izquierda/derecha son nombres de fichero (p.ej. 'cam2.mkv'). El
    audio se toma de 'audio_de' (por defecto, el frontal). 'fps' debe ser el de
    grabacion (de config.json): fija el framerate del mosaico.

    Vuelve enseguida: el proceso corre por su cuenta, con su ventana, y sigue
    aunque se cierre la app. Devuelve el Popen (por si el llamante quiere
    seguirlo), o None si falta algun fichero de entrada o no esta FFmpeg.

    No hay callback: el progreso se ve en la ventana del propio proceso. Se
    escribe en 'mosaico.parcial.mkv' y, solo si FFmpeg termina bien, se renombra
    a 'mosaico.mkv'; asi una interrupcion no deja un mosaico.mkv corrupto.
    """
    # Rutas ABSOLUTAS en todo: el .bat corre con la consola en cualquier cwd, y
    # una ruta relativa se resolveria mal. resolve() ademas normaliza separadores.
    entradas = [(carpeta / frontal).resolve(), (carpeta / izquierda).resolve(),
                (carpeta / derecha).resolve()]
    if not all(f.exists() and f.stat().st_size > 0 for f in entradas):
        return None

    parcial = (carpeta / "mosaico.parcial.mkv").resolve()
    destino = (carpeta / "mosaico.mkv").resolve()
    audio_de = audio_de or frontal
    orden = [frontal, izquierda, derecha]
    idx_audio = orden.index(audio_de) if audio_de in orden else 0

    # El trabajo va en un .bat generado en la carpeta del asalto. En cmd.exe hay
    # que entrecomillar a mano: las rutas (por si tienen espacios) y sobre todo
    # el filter_complex, que contiene &, (), ; y [] -metacaracteres de cmd-. Sin
    # esas comillas, cmd parte el comando y ffmpeg recibe basura. FFmpeg quita
    # las comillas al parsear sus propios argumentos, asi que le llegan intactos.
    #
    # Logica del .bat: ffmpeg escribe el .parcial; si sale con exito, se renombra
    # a mosaico.mkv; si falla o se interrumpe, mosaico.mkv no llega a existir. El
    # propio .bat se borra al final. La ventana se cierra sola al terminar.
    partes = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats",
        "-i", f'"{entradas[0]}"', "-i", f'"{entradas[1]}"', "-i", f'"{entradas[2]}"',
        "-filter_complex", f'"{_filtro(0, 1, 2, fps)}"',
        "-map", '"[out]"',
        # '?' -> no falla si esa cam no tiene audio (p.ej. modo sin micro).
        "-map", f"{idx_audio}:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        f'"{parcial}"',
    ]
    bat = carpeta / "_mosaico.bat"
    contenido = (
        "@echo off\r\n"
        f"title Mosaico {carpeta.name}\r\n"
        f"{' '.join(partes)}\r\n"
        "if errorlevel 1 goto :fin\r\n"
        f'move /Y "{parcial}" "{destino}"\r\n'
        ":fin\r\n"
        f'del "{bat.resolve()}"\r\n'
    )
    bat.write_text(contenido, encoding="ascii")

    try:
        return subprocess.Popen(
            ["cmd", "/c", str(bat.resolve())],
            creationflags=_NUEVA_CONSOLA,
        )
    except FileNotFoundError:
        return None
