"""Subida de las grabaciones a OneDrive mediante rclone."""

import re
import subprocess
import threading
from pathlib import Path
from typing import Callable

_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

_RE_PORCENTAJE = re.compile(r"Transferred:.*?(\d+)%")


def subir(origen: Path, destino: str,
          al_avanzar: Callable[[str, int], None],
          al_terminar: Callable[[bool, str], None]) -> threading.Thread:
    """Copia 'origen' a 'destino' en segundo plano.

    Se usa 'copy' y no 'sync': nunca debe borrar nada del destino.
    """

    def tarea() -> None:
        cmd = [
            "rclone", "copy", str(origen), destino,
            "--progress", "--stats", "1s", "--stats-one-line",
            "--transfers", "3",          # 3 ficheros a la vez, va bien con OneDrive
            "--retries", "5",            # la red de un pabellon suele ser inestable
            "--low-level-retries", "20",
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1,
                creationflags=_SIN_VENTANA,
            )
        except FileNotFoundError:
            al_terminar(False, "rclone no encontrado en el PATH")
            return

        ultima = ""
        assert proc.stdout
        for linea in proc.stdout:
            linea = linea.strip()
            if not linea:
                continue
            ultima = linea
            pct = int(m.group(1)) if (m := _RE_PORCENTAJE.search(linea)) else -1
            al_avanzar(linea, pct)

        ok = proc.wait() == 0
        al_terminar(ok, "Subida completada" if ok else f"Error: {ultima[:150]}")

    hilo = threading.Thread(target=tarea, daemon=True)
    hilo.start()
    return hilo
