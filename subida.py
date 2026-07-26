"""Subida de las grabaciones a la nube mediante rclone.

Se usa el ejecutable de rclone y no la API del proveedor porque la cuenta ya
esta configurada ahi, y rclone se encarga por su cuenta de los reintentos y de
trocear los ficheros grandes. El remoto concreto sale de 'rclone_destino' en
config.json, en formato 'remoto:carpeta', asi que cambiar de proveedor no toca
este modulo: basta configurar otro remoto en rclone y apuntar ahi.

La subida corre en un hilo aparte y va informando por callbacks. Quien los
reciba es responsable de llevarlos a su propio hilo si tiene interfaz grafica
(app.py lo hace con self.after).

ESTRUCTURA DEL MODULO
    subir()         Unica funcion publica: lanza el hilo y devuelve enseguida.
    Progreso        Avance de la subida; se pasa a al_avanzar() para pintarlo.
    _comando()      Arma la linea de rclone.
    _filtros()      --include para la subida selectiva.
    _evento()       Una linea del log JSON como dict (None si no lo es).
    _aplicar_stats() Vuelca un bloque 'stats' en el Progreso.

POR QUE SALIDA JSON
    Se pide a rclone --use-json-log en vez de leer su progreso de texto. El
    formato legible (--stats-one-line) no emite saltos de linea, asi que al
    leerlo linea a linea el avance llegaba a trompicones. Con JSON cada
    actualizacion es una linea completa y trae los datos ya separados: bytes,
    ficheros transferidos y que fichero se esta subiendo en cada momento.
"""

import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ffmpeg_utils import SIN_VENTANA as _SIN_VENTANA


@dataclass
class Progreso:
    """Instantanea del avance de la subida, para pintar en la interfaz."""
    pct: int = 0                 # porcentaje sobre el total de bytes
    ficheros: int = 0            # ficheros ya subidos
    total_ficheros: int = 0
    mb: float = 0.0              # megabytes ya subidos
    total_mb: float = 0.0
    velocidad_mbs: float = 0.0
    eta_s: int | None = None
    asalto: str = ""             # asalto que se esta subiendo ahora mismo

    def resumen(self) -> str:
        """Linea de estado lista para mostrar bajo la barra."""
        partes = [f"{self.mb:.0f}/{self.total_mb:.0f} MB"]
        if self.total_ficheros:
            partes.append(f"{self.ficheros}/{self.total_ficheros} ficheros")
        if self.velocidad_mbs > 0.05:
            partes.append(f"{self.velocidad_mbs:.1f} MB/s")
        if self.eta_s:
            m, s = divmod(int(self.eta_s), 60)
            partes.append(f"faltan {m}m {s:02d}s" if m else f"faltan {s}s")
        linea = "  -  ".join(partes)
        return f"{self.asalto}  ({linea})" if self.asalto else linea


def _asalto_de(ruta: str) -> str:
    """Extrae 'JORNADA/ASALTO' de la ruta relativa que informa rclone.

    rclone da rutas como 'MIERCOLES_22/12_47_ID_027/cam2.mkv'; para el operador
    lo util es el asalto, no el fichero suelto.
    """
    partes = ruta.replace("\\", "/").split("/")
    return "/".join(partes[:2]) if len(partes) >= 2 else ruta


def _evento(linea: str) -> dict | None:
    """Una linea del log de rclone como dict, o None si no es JSON.

    Entre el log JSON se cuelan lineas sueltas de texto (avisos tempranos de
    rclone, antes de que aplique --use-json-log). Se descartan.
    """
    try:
        evento = json.loads(linea)
    except json.JSONDecodeError:
        return None
    return evento if isinstance(evento, dict) else None


def _aplicar_stats(prog: Progreso, stats: dict) -> None:
    """Vuelca un bloque 'stats' de rclone en el Progreso, en sitio.

    Se modifica el Progreso existente en vez de devolver uno nuevo porque la
    interfaz lo lee y lo pinta al momento: no hace falta un historial.
    """
    bytes_hechos = stats.get("bytes", 0)
    total_bytes = stats.get("totalBytes", 0)

    prog.mb = bytes_hechos / 1e6
    prog.total_mb = total_bytes / 1e6
    prog.pct = round(bytes_hechos * 100 / total_bytes) if total_bytes else 0
    prog.ficheros = stats.get("transfers", 0)
    prog.total_ficheros = stats.get("totalTransfers", 0)
    prog.velocidad_mbs = stats.get("speed", 0) / 1e6
    prog.eta_s = stats.get("eta")

    # De los ficheros en vuelo se toma el primero: con --transfers 3 suelen ser
    # las tres camaras del mismo asalto.
    if en_curso := stats.get("transferring"):
        prog.asalto = _asalto_de(en_curso[0].get("name", ""))


def _comando(origen: Path, destino: str, incluye: list[str]) -> list[str]:
    """Linea de rclone para la subida.

    'destino' va en formato de rclone 'remoto:carpeta' (sale de 'rclone_destino'
    en config.json), no es una ruta local. 'incluye' son los filtros ya montados
    por _filtros(): lista vacia = subir 'origen' entero.

    Se usa 'copy' y NUNCA 'sync': sync borraria en el destino todo lo que no
    exista en local, que aqui equivaldria a destruir grabaciones ya subidas.
    """
    return [
        "rclone", "copy", str(origen), destino,
        "--use-json-log", "--log-level", "INFO",
        "--stats", "1s",
        # Ficheros en paralelo. Mantiene la subida por debajo del limite de
        # peticiones del proveedor sin dejar la red ociosa. Estuvo en 3 (una
        # camara por transferencia) cuando el destino era OneDrive; no se ha
        # medido cual es el optimo con Dropbox.
        "--transfers", "4",
        "--retries", "5",            # la red de un pabellon suele ser inestable
        "--low-level-retries", "20",

        # Nunca subir un asalto que todavia se esta grabando: rclone falla al
        # copiar un fichero que crece bajo sus pies. La interfaz ya bloquea el
        # boton mientras graba, pero eso no cubre una segunda instancia de la
        # aplicacion ni una subida lanzada desde fuera. --min-age deja fuera
        # todo lo modificado en los ultimos 30 s.
        "--min-age", "30s",

        # El fichero .subido es una marca LOCAL de estado (la escribe la app al
        # subir con exito); no tiene sentido en el destino, se excluye.
        "--exclude", ".subido",

        *incluye,                    # vacio => sube todo; si no, solo lo elegido
    ]


def _filtros(origen: Path, carpetas: list[Path] | None) -> list[str]:
    """Filtros --include para limitar la subida a ciertos asaltos.

    'carpetas' son rutas de asalto que deben colgar de 'origen' (las de fuera se
    ignoran, no rompen la subida). None o lista vacia devuelve [], que en
    _comando() significa 'subir origen entero'.

    Se filtra en vez de copiar cada carpeta por separado para conservar la
    estructura JORNADA/ASALTO en destino y que baste un solo rclone: una sola
    barra de progreso y un solo --transfers compartido.

    La ruta se pasa con '/' (rclone usa '/' en sus patrones aun en Windows) y
    con '/**' para incluir todo su contenido.
    """
    incluye: list[str] = []
    for c in (carpetas or []):
        try:
            rel = c.relative_to(origen).as_posix()
        except ValueError:
            continue  # una carpeta fuera de 'origen' no se puede incluir
        incluye += ["--include", f"{rel}/**"]
    return incluye


def subir(origen: Path, destino: str,
          al_avanzar: Callable[[Progreso], None],
          al_terminar: Callable[[bool, str], None],
          carpetas: list[Path] | None = None) -> threading.Thread:
    """Copia 'origen' a 'destino' en segundo plano.

    al_avanzar(progreso)     se llama ~1 vez por segundo mientras dure.
    al_terminar(ok, mensaje) se llama exactamente una vez al acabar.

    Ambos se ejecutan en el hilo de rclone, no en el de quien llama.

    'carpetas' limita la subida a esos asaltos concretos (subida selectiva). Si
    es None (o vacia), se sube 'origen' entero. Ver _filtros().
    """
    # El comando se arma ANTES de lanzar el hilo: si algo estuviera mal en los
    # argumentos, el error sale aqui, en el hilo de quien llama, y no enterrado
    # en un hilo de fondo donde nadie lo veria.
    cmd = _comando(origen, destino, _filtros(origen, carpetas))

    def tarea() -> None:
        try:
            proc = subprocess.Popen(
                # rclone emite el log JSON por stderr; se unen ambos canales
                # para leerlo todo en orden por uno solo.
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1,
                creationflags=_SIN_VENTANA,
            )
        except FileNotFoundError:
            al_terminar(False, "rclone no encontrado en el PATH")
            return

        prog = Progreso()
        # Se guardan los mensajes de error porque, si rclone falla, contienen el
        # motivo (sin autorizar, sin red, cuota agotada...). Solo los ultimos 3:
        # si entra en bucle de errores, la lista no crece sin limite.
        errores: list[str] = []

        assert proc.stdout
        for linea in proc.stdout:
            evento = _evento(linea.strip())
            if evento is None:
                continue

            if evento.get("level") == "error":
                errores.append(evento.get("msg", "")[:150])
                del errores[:-3]
            elif stats := evento.get("stats"):
                _aplicar_stats(prog, stats)
                al_avanzar(prog)

        # El bucle termina al cerrarse stdout, o sea al morir rclone.
        if proc.wait() == 0:
            al_terminar(True, "Subida completada")
        else:
            al_terminar(False, f"Error: {errores[-1] if errores else 'fallo desconocido'}")

    # daemon=True: una subida a medias no impide cerrar la aplicacion. Por eso
    # app.py pide confirmacion antes de salir si hay una subida en curso.
    hilo = threading.Thread(target=tarea, daemon=True)
    hilo.start()
    return hilo
