"""Utilidades compartidas para hablar con los binarios de FFmpeg.

Aqui vive lo que TODOS los modulos necesitan al invocar ffmpeg/ffprobe: las
banderas de consola de Windows y las consultas de ffprobe. Se centraliza porque
estaban copiadas en cuatro modulos y empezaban a divergir (ver la nota de
NUEVA_CONSOLA mas abajo).

Se llama 'ffmpeg_utils' y no 'ffmpeg' para que nunca se confunda con el binario
ni con un paquete de terceros del mismo nombre.

QUE NO VA AQUI
    Los comandos de FFmpeg de cada modulo (grabador.comando(),
    mosaico._filtro(), dispositivos.listar_*). Llevan invariantes explicadas en
    DECISIONES.md y se entienden en su contexto; sacarlas de su modulo las haria
    mas dificiles de seguir, no mas modulares. Aqui solo lo compartido de
    verdad.

POR QUE TODO DEVUELVE UN VALOR NEUTRO EN VEZ DE LANZAR
    Estas consultas se usan en mitad de una competicion. Que ffprobe falte, tarde
    o devuelva basura no puede tumbar una grabacion en curso: cada funcion
    devuelve un valor que el llamante ya sabe tratar (0.0, None, False) y la
    decision de que hacer con el se toma arriba.
"""

import subprocess
from pathlib import Path

# Sin ventana: para los procesos de servicio (ffprobe, ffmpeg de grabacion,
# rclone). En Windows evita que aparezca una consola por cada invocacion.
SIN_VENTANA = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

# Consola propia y visible: SOLO para el proceso del mosaico, donde el operador
# quiere ver el progreso de FFmpeg y detectar si se atasca. No usarla para
# ffprobe: son consultas instantaneas que no pintan nada, y cada una abriria una
# ventana negra que se cierra sola (4 parpadeos por mosaico antes de que salga
# la ventana util). Fuera de Windows queda a 0 y hereda la consola actual.
NUEVA_CONSOLA = subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, "CREATE_NEW_CONSOLE") else 0

# Las consultas de ffprobe son instantaneas; si a los 15 s no ha contestado, es
# que algo va mal (fichero en red caida, disco dormido) y es mejor seguir con el
# valor neutro que quedarse esperando.
_TIMEOUT_S = 15


def _preguntar(fichero: Path, *consulta: str) -> str:
    """Ejecuta ffprobe con esa consulta y devuelve su salida cruda ('' si falla).

    'consulta' son los argumentos que van EN MEDIO de la linea de ffprobe: lo
    comun ('-v error' delante, '-of csv=p=0' y el fichero detras) ya lo pone
    esta funcion. Es decir, se pasa solo lo que cambia:

        _preguntar(f, "-show_entries", "format=duration")

    Concentra el manejo de errores: quien llama solo interpreta el texto, y una
    cadena vacia significa siempre 'no se pudo saber'.
    """
    try:
        return subprocess.run(
            ["ffprobe", "-v", "error", *consulta, "-of", "csv=p=0", str(fichero)],
            capture_output=True, text=True, errors="replace",
            timeout=_TIMEOUT_S, creationflags=SIN_VENTANA,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def duracion(fichero: Path) -> float:
    """Segundos que dura un video, via ffprobe. 0.0 si no se puede saber.

    Se pregunta a ffprobe en vez de fiarse del reloj porque lo que importa al
    concatenar es la duracion REAL del contenedor. Devuelve 0.0 tambien cuando
    el fichero quedo mal cerrado (duracion 'N/A'), y quien llama lo trata como
    'no declarar hueco', que es lo prudente.
    """
    try:
        return float(_preguntar(fichero, "-show_entries", "format=duration"))
    except ValueError:
        return 0.0


def duracion_real(fichero: Path) -> float:
    """Duracion fiable incluso si el contenedor quedo mal cerrado.

    duracion() lee la cabecera, que es instantaneo pero devuelve 0.0 cuando el
    fichero se corto sin cerrar (proceso muerto de golpe: ffprobe informa 'N/A').
    Esos trozos SI tienen video; solo les falta el dato en la cabecera.

    Cuando pasa eso, se cuenta el numero real de frames y se divide por la
    cadencia. Es mas lento porque hay que decodificar, asi que solo se recurre a
    ello si la lectura rapida no da nada.

    Importa al unir los trozos de una camara relanzada: si un trozo cuenta como
    0 s, el hueco negro que se calcula para cubrir la caida sale inflado por esos
    segundos y el video acaba MAS LARGO que el de las camaras sanas, que es
    justo la desincronizacion que la union pretende evitar.
    """
    rapida = duracion(fichero)
    if rapida > 0:
        return rapida

    salida = _preguntar(
        fichero, "-count_frames", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,nb_read_frames",
    )
    # ffprobe devuelve los campos en el orden en que los declara el stream, no
    # en el que se piden: se localiza cada uno por su forma ('num/den' es la
    # cadencia; el entero suelto, los frames).
    cadencia = frames = None
    for campo in salida.replace("\n", ",").split(","):
        campo = campo.strip()
        if "/" in campo:
            try:
                num, den = campo.split("/")
                cadencia = float(num) / float(den) if float(den) else None
            except (ValueError, ZeroDivisionError):
                pass
        elif campo.isdigit():
            frames = int(campo)

    if frames and cadencia:
        return frames / cadencia
    return 0.0


def muestra_audio(fichero: Path) -> int | None:
    """Frecuencia de muestreo del audio principal. None si no hay o no se sabe.

    Se usa al generar el tramo negro para que el silencio tenga el mismo
    formato que el resto del asalto y el demuxer concat no tenga que mezclar
    streams con distinta frecuencia.
    """
    salida = _preguntar(fichero, "-select_streams", "a:0",
                        "-show_entries", "stream=sample_rate")
    try:
        return int(salida) if salida else None
    except ValueError:
        return None


def tiene_audio(fichero: Path) -> bool:
    """True si el fichero contiene al menos una pista de audio."""
    return bool(_preguntar(fichero, "-select_streams", "a:0",
                           "-show_entries", "stream=index"))
