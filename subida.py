"""Subida de las grabaciones a OneDrive mediante rclone.

Se usa el ejecutable de rclone y no una API de OneDrive porque la cuenta ya
esta configurada ahi (remoto "personal:"), y rclone se encarga por su cuenta de
los reintentos y de trocear los ficheros grandes.

La subida corre en un hilo aparte y va informando por callbacks. Quien los
reciba es responsable de llevarlos a su propio hilo si tiene interfaz grafica
(app.py lo hace con self.after).

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

_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


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

    rclone da rutas como 'MIERCOLES_22/007_Garcia_Lopez/cam2.mkv'; para el
    operador lo util es el asalto, no el fichero suelto.
    """
    partes = ruta.replace("\\", "/").split("/")
    return "/".join(partes[:2]) if len(partes) >= 2 else ruta


def subir(origen: Path, destino: str,
          al_avanzar: Callable[[Progreso], None],
          al_terminar: Callable[[bool, str], None],
          carpetas: list[Path] | None = None) -> threading.Thread:
    """Copia 'origen' a 'destino' en segundo plano.

    al_avanzar(progreso)     se llama ~1 vez por segundo mientras dure.
    al_terminar(ok, mensaje) se llama exactamente una vez al acabar.

    Ambos se ejecutan en el hilo de rclone, no en el de quien llama.

    'carpetas' limita la subida a esos asaltos concretos (subida selectiva). Si
    es None (o vacia), se sube 'origen' entero. Se implementa con --include sobre
    la ruta relativa de cada carpeta, no copiando cada una por separado: asi se
    conserva la estructura JORNADA/ASALTO en destino y basta un solo rclone (una
    sola barra de progreso, un solo --transfers compartido).

    Se usa 'copy' y NUNCA 'sync': sync borraria en OneDrive todo lo que no
    exista en local, que aqui equivaldria a destruir grabaciones ya subidas.
    """

    # Filtros --include con la ruta relativa de cada carpeta seleccionada. La
    # ruta se pasa con '/' (rclone usa '/' en sus patrones aun en Windows) y con
    # '/**' para incluir todo su contenido.
    incluye: list[str] = []
    for c in (carpetas or []):
        try:
            rel = c.relative_to(origen).as_posix()
        except ValueError:
            continue  # una carpeta fuera de 'origen' no se puede incluir; se ignora
        incluye += ["--include", f"{rel}/**"]

    def tarea() -> None:
        cmd = [
            "rclone", "copy", str(origen), destino,
            "--use-json-log", "--log-level", "INFO",
            "--stats", "1s",             # una actualizacion por segundo
            "--transfers", "3",          # 3 ficheros a la vez, va bien con OneDrive
            "--retries", "5",            # la red de un pabellon suele ser inestable
            "--low-level-retries", "20",

            # Nunca subir un asalto que todavia se esta grabando: rclone falla
            # al copiar un fichero que crece bajo sus pies. La interfaz ya
            # bloquea el boton mientras graba, pero eso no cubre una segunda
            # instancia de la aplicacion ni una subida lanzada desde fuera.
            # --min-age deja fuera todo lo modificado en los ultimos 30 s.
            "--min-age", "30s",

            # El fichero .subido es una marca LOCAL de estado (la escribe la app
            # al subir con exito); no tiene sentido en OneDrive, se excluye.
            "--exclude", ".subido",

            *incluye,                    # vacio => sube todo; si no, solo lo elegido
        ]
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
        # motivo (sin autorizar, sin red, cuota agotada...).
        errores: list[str] = []

        assert proc.stdout
        for linea in proc.stdout:
            linea = linea.strip()
            if not linea:
                continue

            try:
                evento = json.loads(linea)
            except json.JSONDecodeError:
                # Alguna linea suelta no JSON (avisos tempranos de rclone).
                continue

            if evento.get("level") == "error":
                errores.append(evento.get("msg", "")[:150])
                del errores[:-3]
                continue

            stats = evento.get("stats")
            if not stats:
                continue

            total_bytes = stats.get("totalBytes", 0)
            prog.mb = stats.get("bytes", 0) / 1e6
            prog.total_mb = total_bytes / 1e6
            prog.pct = round(stats.get("bytes", 0) * 100 / total_bytes) if total_bytes else 0
            prog.ficheros = stats.get("transfers", 0)
            prog.total_ficheros = stats.get("totalTransfers", 0)
            prog.velocidad_mbs = stats.get("speed", 0) / 1e6
            prog.eta_s = stats.get("eta")

            # De los ficheros en vuelo se toma el primero: con --transfers 3
            # suelen ser las tres camaras del mismo asalto.
            if en_curso := stats.get("transferring"):
                prog.asalto = _asalto_de(en_curso[0].get("name", ""))

            al_avanzar(prog)

        # El bucle termina al cerrarse stdout, o sea al morir rclone.
        ok = proc.wait() == 0
        if ok:
            al_terminar(True, "Subida completada")
        else:
            al_terminar(False, f"Error: {errores[-1] if errores else 'fallo desconocido'}")

    # daemon=True: una subida a medias no impide cerrar la aplicacion. Por eso
    # app.py pide confirmacion antes de salir si hay una subida en curso.
    hilo = threading.Thread(target=tarea, daemon=True)
    hilo.start()
    return hilo
