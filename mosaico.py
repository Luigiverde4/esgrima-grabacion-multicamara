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

import ffmpeg_utils
from ffmpeg_utils import NUEVA_CONSOLA as _NUEVA_CONSOLA

# Consultas de ffprobe compartidas con el grabador. Van SIN ventana (dentro de
# ffmpeg_utils): son instantaneas y no pintan nada, asi que abrirles una consola
# solo producia parpadeos de ventanas negras antes de la del mosaico. La consola
# visible es la de FFmpeg, mas abajo, que es la que el operador quiere ver.
_duracion = ffmpeg_utils.duracion
_tiene_audio = ffmpeg_utils.tiene_audio

# Lienzo y geometria. El frontal ocupa la franja superior (720 de alto), los
# laterales se reparten la inferior (360 de alto) a partes iguales.
_ANCHO, _ALTO = 1920, 1080
_ALTO_SUP = 720
_ALTO_INF = _ALTO - _ALTO_SUP          # 360


def _celda(idx: int, ancho: int, alto: int, etiqueta: str, fps: int) -> str:
    """Escala una entrada a una celda 'ancho x alto' SIN deformar.

    'idx' es la posicion de la entrada en la linea de FFmpeg (el 0 de '[0:v]'),
    y 'etiqueta' el nombre con el que la rama queda disponible para el overlay
    ('top', 'bl', 'br'). Devuelve un fragmento de filter_complex terminado en
    ';', pensado para concatenarse con los demas.

    force_original_aspect_ratio=decrease conserva la proporcion (una cam 16:9
    nunca se estira); el pad rellena con negro hasta el tamano exacto de la
    celda y centra la imagen. setsar=1 evita que overlay descoloque nada.

    'fps={fps}' PRIMERO, antes de escalar, y aqui en cada entrada en vez de solo
    al final: normaliza la cadencia de cada camara por separado. Es lo que salva
    el caso de una camara relanzada, que llega con menos frames de los que
    declara (huecos + perdidas por USB): sin esto, el overlay tiene que casar
    entradas de cadencia muy distinta, el proceso se hincha a mas de 1 GB de RAM
    y baja a ~0.4x de velocidad. Normalizando antes, cada rama entra ya a fps
    constante y el overlay solo empareja frames uno a uno.

    Ademas, escalar despues de fijar el fps evita escalar frames que luego se
    descartan: con una camara a 12 fps efectivos eso es la mitad del trabajo.
    """
    return (
        f"[{idx}:v]fps={fps},scale={ancho}:{alto}:force_original_aspect_ratio=decrease,"
        f"pad={ancho}:{alto}:(ow-iw)/2:(oh-ih)/2,setsar=1[{etiqueta}];"
    )


def _filtro(frontal_idx: int, izq_idx: int, der_idx: int, fps: int) -> str:
    """Cadena filter_complex para el mosaico.

    Los tres indices son la posicion de cada entrada -i (0, 1, 2), no ficheros:
    quien llama decide que POV va en cada sitio pasando el indice que le
    corresponde. 'fps' es el de grabacion y se aplica a cada rama y a la salida.

    Cada POV se ajusta a su celda respetando la proporcion 16:9 (letterbox en
    negro si hace falta), asi ninguna imagen se estira. El frontal (1280x720) se
    centra arriba; los laterales (960x360) llenan cada mitad inferior.

    Tres detalles imprescindibles (ver bugs en el docstring del modulo):
      - 'shortest=1' en el primer overlay: el fondo 'color' es infinito y sin
        esto el fichero sale sin duracion. Se corta solo al final de la entrada
        superior; no recorta video util porque las tres ramas ya estan alineadas
        y las caidas se declaran como huecos al concatenar.
      - 'eof_action=pass' en los overlays de los laterales: sin el, si una
        camara es MAS CORTA que las otras (pasa al relanzarla tras una caida:
        pierde el tramo caido), el overlay se queda esperando frames que no
        llegan y el proceso se cuelga acumulando memoria en vez de terminar.
        Con 'pass', al agotarse un lateral se sigue con lo que haya debajo.
      - 'fps={fps}' al final: fija el framerate real; sin el, FFmpeg pone 25.
    """
    x_frontal = (_ANCHO - 1280) // 2   # centra el frontal: 320
    return (
        _celda(frontal_idx, 1280, _ALTO_SUP, "top", fps)
        + _celda(izq_idx, 960, _ALTO_INF, "bl", fps)
        + _celda(der_idx, 960, _ALTO_INF, "br", fps)
        # Las tres ramas se unen sobre un fondo negro FINITO: 'color' con -t
        # implicito no existe, asi que la duracion la marca el ultimo overlay
        # con shortest=0 y eof_action=pass -> dura lo que la entrada MAS LARGA.
        + f"color=c=black:s={_ANCHO}x{_ALTO}:r={fps}[bg];"
        # shortest=0 en los tres: el mosaico NO debe cortarse con la camara mas
        # corta. Si una se cayo y su video acaba antes, las otras dos tienen que
        # llegar hasta su final; la que falta se queda en negro (eof_action=pass
        # mantiene el ultimo estado del fondo, que es negro).
        + f"[bg][top]overlay=x={x_frontal}:y=0:shortest=0:eof_action=pass[a];"
        + f"[a][bl]overlay=x=0:y={_ALTO_SUP}:shortest=0:eof_action=pass[b];"
        + f"[b][br]overlay=x=960:y={_ALTO_SUP}:shortest=0:eof_action=pass,"
        + f"fps={fps}[out]"
    )


def generar(carpeta: Path, frontal: str, izquierda: str, derecha: str,
            fps: int = 30, audio_de: str | None = None) -> subprocess.Popen | None:
    """Lanza la generacion de 'mosaico.mkv' en un proceso con ventana propia.

    'carpeta' es la del asalto: de ahi salen las entradas y ahi se escribe el
    resultado. frontal/izquierda/derecha son NOMBRES DE FICHERO sueltos (p.ej.
    'cam2.mkv'), no rutas, y se resuelven contra 'carpeta'.

    'fps' debe ser el de grabacion (config.json): fija el framerate del mosaico.
    'audio_de' es el nombre de fichero del que sacar el audio; si es None, o no
    esta entre los tres, se usa el frontal.

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
    hay_audio = _tiene_audio(entradas[idx_audio])

    # Duracion de la salida = la de la entrada MAS LARGA. Hace falta pasarla
    # explicitamente con -t por dos motivos que van juntos:
    #
    #   - Los overlays llevan shortest=0 para que el mosaico NO se corte con la
    #     camara mas corta. Si una se cayo y su video acaba antes, las otras dos
    #     tienen que llegar a su final; antes el mosaico terminaba con la corta y
    #     las demas nunca se veian enteras.
    #   - Pero el fondo 'color' es una fuente INFINITA: sin shortest=1 y sin -t,
    #     FFmpeg no termina nunca (queda 'duration=N/A' y el proceso colgado).
    #     Ver ERRORES_CONOCIDOS.md.
    #
    # -t cierra las dos cosas: el mosaico dura lo que la entrada mas larga y el
    # color deja de ser un problema. Si no se puede medir ninguna (ffprobe
    # ausente), se cae a 0 y mas abajo se omite -t: mejor un mosaico que se corta
    # con la mas corta que ninguno.
    duracion_max = max((_duracion(f) for f in entradas), default=0.0)

    # El trabajo va en un .bat generado en la carpeta del asalto. En cmd.exe hay
    # que entrecomillar a mano: las rutas (por si tienen espacios) y sobre todo
    # el filter_complex, que contiene &, (), ; y [] -metacaracteres de cmd-. Sin
    # esas comillas, cmd parte el comando y ffmpeg recibe basura. FFmpeg quita
    # las comillas al parsear sus propios argumentos, asi que le llegan intactos.
    #
    # Logica del .bat: ffmpeg escribe el .parcial; si sale con exito, se renombra
    # a mosaico.mkv; si falla o se interrumpe, mosaico.mkv no llega a existir. El
    # propio .bat se borra al final. La ventana se cierra sola al terminar.
    args_ffmpeg = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats",
        "-i", f'"{entradas[0]}"', "-i", f'"{entradas[1]}"', "-i", f'"{entradas[2]}"',
        "-filter_complex", f'"{_filtro(0, 1, 2, fps)}"',
        "-map", '"[out]"',
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
    ]
    if duracion_max > 0:
        # Acota la salida: sin esto el fondo 'color' no termina nunca.
        args_ffmpeg += ["-t", f"{duracion_max:.3f}"]
    if hay_audio:
        # Solo se incluye audio si la fuente realmente tiene una pista.
        # Asi evitamos el aviso de FFmpeg cuando el mosaico sale mudo.
        args_ffmpeg += ["-map", f"{idx_audio}:a?", "-c:a", "aac", "-b:a", "160k"]
    args_ffmpeg.append(f'"{parcial}"')

    bat = carpeta / "_mosaico.bat"

    # El cierre del .bat es delicado y esta medido, no adivinado:
    #
    # Un 'del "<el propio bat>"' a secas hace que cmd borre el fichero que aun
    # esta leyendo; al ir a por la linea siguiente ya no existe, imprime "The
    # batch file cannot be found" y SALE CON CODIGO 1 aunque todo haya ido bien.
    # El mosaico salia correcto, pero el proceso se declaraba fallido: quien
    # mirara returncode veria un fallo inexistente (o, peor, tomaria por bueno
    # el 1 y dejaria de distinguir el fallo real).
    #
    # '(goto) 2>nul' cierra el contexto del batch antes de borrar, y el
    # 'exit /b %CODIGO%' devuelve el codigo guardado. Comprobado con las cuatro
    # combinaciones (exito/fallo x borrado/no borrado): es la unica forma que
    # da 0 al terminar bien, distinto de 0 al fallar, y borra el .bat siempre.
    #
    # %CODIGO% se captura DESPUES de cada paso porque 'if errorlevel' no lo
    # conserva: sin guardarlo, el codigo que llega al final es el del ultimo
    # comando ejecutado (el propio del), no el de FFmpeg.
    contenido = (
        "@echo off\r\n"
        f"title Mosaico {carpeta.name}\r\n"
        f"{' '.join(args_ffmpeg)}\r\n"
        "set CODIGO=%errorlevel%\r\n"
        "if not %CODIGO%==0 goto :fin\r\n"
        f'move /Y "{parcial}" "{destino}" >nul\r\n'
        "set CODIGO=%errorlevel%\r\n"
        ":fin\r\n"
        f'endlocal & (goto) 2>nul & (del "{bat.resolve()}" & exit /b %CODIGO%)\r\n'
    )
    bat.write_text(contenido, encoding="ascii")

    try:
        return subprocess.Popen(
            ["cmd", "/c", str(bat.resolve())],
            creationflags=_NUEVA_CONSOLA,
        )
    except FileNotFoundError:
        return None
