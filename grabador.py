"""Motor de grabacion: gestiona un proceso FFmpeg por camara.

Cada camara graba de forma independiente. Si una falla, las demas siguen.
Se graba en MKV porque soporta cortes abruptos sin corromper el fichero.
"""

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# En Windows, evita que se abra una ventana de consola por cada FFmpeg.
_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


@dataclass
class Camara:
    id: str
    nombre: str
    dispositivo: str | None = None  # None => se usa testsrc

    @property
    def configurada(self) -> bool:
        return bool(self.dispositivo)


@dataclass
class EstadoCamara:
    """Lo que la GUI necesita saber de una camara en curso."""
    grabando: bool = False
    frames: int = 0
    error: str | None = None
    fichero: Path | None = None
    ultimo_avance: float = field(default_factory=time.monotonic)

    @property
    def bloqueada(self) -> bool:
        """Grabando pero sin frames nuevos en 5s: la capturadora se ha caido."""
        if not self.grabando or self.frames == 0:
            return False
        return (time.monotonic() - self.ultimo_avance) > 5.0


class GrabadorCamara:
    """Envuelve un proceso FFmpeg y sigue su progreso."""

    _RE_FRAME = re.compile(r"frame=\s*(\d+)")

    # Prefijos de las lineas de telemetria de -progress.
    _TELEMETRIA = ("bitrate=", "total_size=", "out_time", "speed=", "fps=",
                   "dup_frames=", "drop_frames=", "progress=", "stream_")

    # Avisos benignos de FFmpeg que no deben marcarse como fallo de camara:
    # generarian falsas alarmas en mitad de la competicion.
    _RUIDO = ("fontconfig", "deprecated", "last message repeated",
              "non-monotonic", "past duration", "vbv underflow")

    @classmethod
    def _es_ruido(cls, linea: str) -> bool:
        bajo = linea.lower()
        return any(r in bajo for r in cls._RUIDO)

    def __init__(self, camara: Camara, cfg: dict):
        self.camara = camara
        self.cfg = cfg
        self.estado = EstadoCamara()
        self._proc: subprocess.Popen | None = None
        self._hilo: threading.Thread | None = None
        self._ultimas_lineas: list[str] = []
        self._detencion_pedida = False  # distingue parada nuestra de caida real

    def _entrada(self) -> list[str]:
        """Argumentos de entrada segun sea testsrc o capturadora real."""
        v = self.cfg["video"]
        if not self.camara.configurada:
            # Patron de prueba para validar sin hardware. testsrc2 ya incluye
            # un contador de tiempo propio, asi evitamos drawtext (que en
            # Windows depende de fontconfig y suele no estar disponible).
            fuente = f"testsrc2=size={v['resolucion']}:rate={v['fps']}"
            # -re fuerza ritmo de tiempo real; sin el, lavfi genera frames a la
            # velocidad de la CPU y la duracion grabada no coincide con la real.
            return ["-re", "-f", "lavfi", "-i", fuente]

        # Capturadora real via DirectShow.
        return [
            "-f", "dshow",
            "-rtbufsize", "256M",          # colchon ante microcortes USB
            "-video_size", v["resolucion"],
            "-framerate", str(v["fps"]),
            "-i", f"video={self.camara.dispositivo}",
        ]

    def comando(self, destino: Path) -> list[str]:
        v = self.cfg["video"]
        return [
            # Sin -nostdin: necesitamos enviar 'q' para que FFmpeg cierre
            # el contenedor correctamente y escriba la duracion.
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-progress", "pipe:2", "-stats_period", "0.5",
            *self._entrada(),
            "-an",                          # sin audio, de momento
            "-c:v", "libx264",
            "-preset", v["preset"],
            "-crf", str(v["crf"]),
            "-pix_fmt", "yuv420p",          # compatibilidad de reproduccion
            "-g", str(v["fps"] * 2),        # keyframe cada 2s, facilita recortes
            "-y", str(destino),
        ]

    def iniciar(self, carpeta: Path) -> None:
        destino = carpeta / f"{self.camara.id}.mkv"
        self.estado = EstadoCamara(grabando=True, fichero=destino)
        self._ultimas_lineas = []
        self._detencion_pedida = False

        try:
            self._proc = subprocess.Popen(
                self.comando(destino),
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stdin=subprocess.PIPE,
                text=True,
                errors="replace",
                bufsize=1,
                creationflags=_SIN_VENTANA,
            )
        except FileNotFoundError:
            self.estado.grabando = False
            self.estado.error = "FFmpeg no encontrado en el PATH"
            return

        self._hilo = threading.Thread(target=self._leer_progreso, daemon=True)
        self._hilo.start()

    def _leer_progreso(self) -> None:
        """Lee la salida de FFmpeg para detectar avance y errores."""
        assert self._proc and self._proc.stderr
        for linea in self._proc.stderr:
            linea = linea.strip()
            if not linea:
                continue

            if m := self._RE_FRAME.search(linea):
                self.estado.frames = int(m.group(1))
                self.estado.ultimo_avance = time.monotonic()
            elif not linea.startswith(self._TELEMETRIA) and not self._es_ruido(linea):
                # Lo que no es telemetria ni ruido conocido, es un error real.
                self._ultimas_lineas.append(linea)
                del self._ultimas_lineas[:-5]

        codigo = self._proc.wait()
        self.estado.grabando = False

        # Al detener nosotros la grabacion, FFmpeg puede salir con codigos
        # distintos de cero de forma legitima: solo es fallo si ademas
        # informo de algo o si el proceso murio por su cuenta.
        if self._detencion_pedida:
            if self._ultimas_lineas and not self.estado.error:
                self.estado.error = self._ultimas_lineas[-1][:120]
        elif codigo != 0 and not self.estado.error:
            detalle = self._ultimas_lineas[-1] if self._ultimas_lineas else f"codigo {codigo}"
            self.estado.error = detalle[:120]

    def detener(self) -> None:
        """Cierra FFmpeg dejandole finalizar el fichero correctamente.

        Enviar 'q' es lo que permite que se escriba la duracion en el MKV.
        terminate() se reserva como plan B si no responde.
        """
        self._detencion_pedida = True
        if not self._proc or self._proc.poll() is not None:
            return

        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.write("q")
                self._proc.stdin.flush()
                self._proc.stdin.close()
            self._proc.wait(timeout=8)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

        if self._hilo:
            self._hilo.join(timeout=3)


class Sesion:
    """Coordina las tres camaras y la numeracion de asaltos."""

    def __init__(self, ruta_cfg: Path):
        self.ruta_cfg = ruta_cfg
        self.cfg = json.loads(ruta_cfg.read_text(encoding="utf-8"))
        self.camaras = [Camara(**c) for c in self.cfg["camaras"]]
        self.raiz = Path(self.cfg["carpeta_grabaciones"])
        self.raiz.mkdir(parents=True, exist_ok=True)

        self.grabadores: list[GrabadorCamara] = []
        self.asalto_actual: dict | None = None

    def siguiente_numero(self) -> int:
        """Deduce el numero de asalto de las carpetas ya existentes.

        Asi la numeracion sobrevive a un reinicio de la aplicacion.
        """
        maximo = 0
        for d in self.raiz.glob("asalto_*"):
            if d.is_dir() and (m := re.match(r"asalto_(\d+)", d.name)):
                maximo = max(maximo, int(m.group(1)))
        return maximo + 1

    @staticmethod
    def _limpiar(texto: str) -> str:
        """Convierte un texto libre en algo valido como nombre de carpeta."""
        limpio = re.sub(r"[^\w\s-]", "", texto, flags=re.UNICODE).strip()
        return re.sub(r"[\s]+", "-", limpio)[:60]

    @property
    def grabando(self) -> bool:
        return self.asalto_actual is not None

    def iniciar_asalto(self, etiqueta: str = "") -> dict:
        if self.grabando:
            raise RuntimeError("Ya hay un asalto en curso")

        numero = self.siguiente_numero()
        sufijo = self._limpiar(etiqueta)
        nombre = f"asalto_{numero:03d}" + (f"_{sufijo}" if sufijo else "")
        carpeta = self.raiz / nombre
        carpeta.mkdir(parents=True, exist_ok=True)

        self.grabadores = [GrabadorCamara(c, self.cfg) for c in self.camaras]
        for g in self.grabadores:
            g.iniciar(carpeta)

        self.asalto_actual = {
            "numero": numero,
            "etiqueta": etiqueta,
            "carpeta": carpeta,
            "inicio": datetime.now(),
        }
        return self.asalto_actual

    def detener_asalto(self) -> dict:
        if not self.asalto_actual:
            raise RuntimeError("No hay ningun asalto en curso")

        for g in self.grabadores:
            g.detener()

        info = self.asalto_actual
        fin = datetime.now()
        duracion = (fin - info["inicio"]).total_seconds()

        metadata = {
            "competicion": self.cfg["competicion"],
            "asalto": info["numero"],
            "etiqueta": info["etiqueta"],
            "inicio": info["inicio"].isoformat(timespec="seconds"),
            "fin": fin.isoformat(timespec="seconds"),
            "duracion_s": round(duracion, 1),
            "modo_prueba": not any(c.configurada for c in self.camaras),
            "camaras": [
                {
                    "id": g.camara.id,
                    "nombre": g.camara.nombre,
                    "fichero": g.estado.fichero.name if g.estado.fichero else None,
                    "frames": g.estado.frames,
                    "tamano_bytes": (
                        g.estado.fichero.stat().st_size
                        if g.estado.fichero and g.estado.fichero.exists() else 0
                    ),
                    "error": g.estado.error,
                }
                for g in self.grabadores
            ],
        }
        (info["carpeta"] / "metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        self.asalto_actual = None
        self.grabadores = []
        return metadata
