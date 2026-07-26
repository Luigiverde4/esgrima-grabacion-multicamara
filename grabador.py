"""Motor de grabacion: gestiona un proceso FFmpeg por camara.

Cada camara graba de forma independiente. Si una falla, las demas siguen.
Se graba en MKV porque soporta cortes abruptos sin corromper el fichero.

ESTRUCTURA DEL MODULO
    Camara          Definicion estatica de una camara (viene de config.json).
    EstadoCamara    Estado vivo de una grabacion en curso; la GUI lee esto.
    GrabadorCamara  Envuelve UN proceso FFmpeg y vigila su salida.
    Sesion          Coordina las tres camaras y numera los asaltos.

COMO SE COMUNICA CON LA INTERFAZ
    No hay callbacks hacia la GUI. Cada GrabadorCamara escribe en su
    EstadoCamara y la interfaz lo consulta cada 500 ms. Se hace asi porque
    Tkinter no admite que se toquen sus widgets desde otro hilo, y aqui hay
    un hilo por camara leyendo la salida de FFmpeg.

    Este modulo no importa Tkinter ni sabe que existe una interfaz: se podria
    controlar desde una web o un pedal sin tocar nada de aqui.
"""

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import dispositivos
import ffmpeg_utils
from ffmpeg_utils import SIN_VENTANA as _SIN_VENTANA

# Consultas de ffprobe: viven en ffmpeg_utils.py porque las comparte el mosaico.
#
# Al unir trozos se usa duracion_real (cuenta frames si el contenedor quedo mal
# cerrado) y no duracion: un trozo cortado de golpe informa 'N/A', valdria 0 s y
# el hueco negro saldria inflado por esos segundos. Ver _unir_trozos().
_duracion = ffmpeg_utils.duracion_real
_muestra_audio = ffmpeg_utils.muestra_audio


@dataclass
class Camara:
    """Una camara tal y como esta declarada en config.json."""
    id: str                          # identifica el fichero: cam1.mkv
    nombre: str                      # descripcion para el operador: "Frontal"
    dispositivo: str | None = None   # id de hardware del video; None => modo prueba
    dispositivo_nombre: str | None = None  # nombre visible del video, para mostrar
    audio: str | None = None         # id/nombre DirectShow del micro; None => sin audio
    formato: str = "mjpeg"           # mjpeg | yuyv422 | auto (formato de entrada)

    @property
    def configurada(self) -> bool:
        """False mientras no haya capturadora asignada (se usara testsrc2)."""
        return bool(self.dispositivo)

    @property
    def con_audio(self) -> bool:
        """True si hay micro asignado. Solo tiene efecto con video real."""
        return self.configurada and bool(self.audio)


@dataclass
class EstadoCamara:
    """Estado vivo de una camara. Lo escribe el hilo lector, lo lee la GUI.

    No lleva bloqueo de concurrencia: son asignaciones sueltas a atributos, y
    en Python cada una es atomica. Como la GUI solo lee para pintar, leer un
    valor una decima tarde no tiene ninguna consecuencia.
    """
    grabando: bool = False
    frames: int = 0                  # ultimo 'frame=N' informado por FFmpeg
    error: str | None = None
    fichero: Path | None = None
    ultimo_avance: float = field(default_factory=time.monotonic)

    # Segundos sin avance de frames antes de dar la camara por congelada.
    #
    # FFmpeg informa cada 0.5 s (-stats_period en comando()), asi que 2 s son
    # tres informes perdidos. El valor sale de medir el hueco real entre
    # informes: 0.52 s en reposo y 0.64 s con las 16 CPU saturadas. Dos
    # segundos dejan margen para un informe retrasado suelto (un microcorte de
    # USB, un pico de disco) sin encender la alarma.
    #
    # No bajarlo a 1 s sin volver a medir CON CAPTURADORAS REALES: en yuyv422 a
    # 1080p el bus USB va justo (de ahi el aviso 'real-time buffer' de _RUIDO,
    # que ya marco las tres camaras como caidas en un asalto valido). Una falsa
    # alarma empuja al operador a relanzar, y relanzar sin motivo SI destruye
    # valor: corta el trozo e inserta un hueco negro en una grabacion sana.
    UMBRAL_CONGELADA_S = 2.0

    @property
    def bloqueada(self) -> bool:
        """Detecta la capturadora congelada: el fallo tipico del HDMI suelto.

        No basta con mirar si el proceso vive. Cuando se afloja el cable, FFmpeg
        sigue corriendo y el fichero sigue creciendo, pero la imagen se queda
        quieta. Lo que lo delata es que el contador de frames deja de avanzar.

        Se exige frames > 0 para no dar la alarma durante el arranque, que en
        una capturadora USB puede tardar un par de segundos.
        """
        if not self.grabando or self.frames == 0:
            return False
        return (time.monotonic() - self.ultimo_avance) > self.UMBRAL_CONGELADA_S


class GrabadorCamara:
    """Envuelve un proceso FFmpeg y sigue su progreso.

    Se lanza un proceso por camara en lugar de uno solo con tres entradas.
    Cuesta mas codigo, pero un unico proceso seria un unico punto de fallo:
    al desconectarse una capturadora, FFmpeg abortaria y se perderian los tres
    POVs del asalto en vez de uno. En un evento irrepetible eso lo justifica.

    El precio es que los tres arrancan escalonados (~1 s). Irrelevante para
    revision tecnica; para montaje sincronizado al frame haria falta claqueta.
    """

    # Anclado al principio de linea a proposito: '-progress' siempre emite
    # 'frame=N' al inicio. Sin el ancla, un error que mencione 'frame=' (p.ej.
    # "Error while decoding stream #0:0: frame= 12") se contaria como avance:
    # la linea no llegaria a _ultimas_lineas y ademas refrescaria ultimo_avance,
    # desactivando la deteccion de imagen congelada. Es decir, daria una camara
    # por buena cuando no lo esta, que es peor que una falsa alarma.
    _RE_FRAME = re.compile(r"^frame=\s*(\d+)")

    # Prefijos de las lineas de telemetria de -progress.
    _TELEMETRIA = ("bitrate=", "total_size=", "out_time", "speed=", "fps=",
                   "dup_frames=", "drop_frames=", "progress=", "stream_")

    # Avisos benignos de FFmpeg que no deben marcarse como fallo de camara:
    # generarian falsas alarmas en mitad de la competicion.
    #
    # Al anadir entradas aqui, comprobar que no tapan un fallo real: una camara
    # marcada como correcta cuando no graba es peor que una falsa alarma.
    # 'real-time buffer ... too full ... frame dropped!': dshow avisa de que se
    # le lleno el buffer y perdio algun frame. La grabacion CONTINUA y el fichero
    # queda correcto, asi que no es un fallo de camara. Es habitual en yuyv422 a
    # 1080p (sin comprimir satura el USB) y marcaba las tres camaras como caidas
    # en un asalto perfectamente valido.
    #
    # No tapa un fallo real: si la camara dejara de dar imagen, el contador de
    # frames se detiene y EstadoCamara.bloqueada lo delata igual. La perdida de
    # frames que este aviso denuncia se ve ademas en 'frames' de metadata.json
    # (ver ERRORES_CONOCIDOS.md: frames dispares = USB corto).
    _RUIDO = ("fontconfig", "deprecated", "last message repeated",
              "non-monotonic", "past duration", "vbv underflow",
              "real-time buffer")

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

        # Ficheros grabados por esta camara en el asalto, en orden. Normalmente
        # uno; hay mas si el operador relanzo tras una caida. Al detener se
        # concatenan en uno solo (ver Sesion._unir_trozos).
        self.trozos: list[Path] = []
        self.frames_previos = 0   # frames de los trozos ya cerrados
        self.carpeta: Path | None = None

        # Segundos que la camara estuvo caida antes de cada trozo, en paralelo a
        # self.trozos (el primero siempre 0.0). Se declaran como hueco al unir,
        # para que el video conserve la sincronia con las demas camaras.
        self.huecos: list[float] = []
        self._fin_trozo: float | None = None   # monotonic al morir el ultimo trozo

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

        # Capturadora real via DirectShow. Si hay micro asignado, se captura en
        # el mismo -i con la sintaxis 'video=X:audio=Y'; el separador ':' es
        # seguro porque los nombres DirectShow no lo contienen.
        especificador = f"video={self.camara.dispositivo}"
        if self.camara.con_audio:
            especificador += f":audio={self.camara.audio}"

        # Formato de entrada. Por defecto FFmpeg coge YUYV sin comprimir (~1.5
        # Gbps a 1080p): tres capturadoras asi saturan el USB 3.0. Pidiendo
        # MJPEG explicitamente entra comprimido y las tres caben.
        #   mjpeg    -> -vcodec mjpeg
        #   yuyv422  -> -pixel_format yuyv422 (rawvideo se fija por pixel_format)
        #   auto     -> nada, decide FFmpeg
        seleccion_formato: list[str] = []
        if self.camara.formato == "mjpeg":
            seleccion_formato = ["-vcodec", "mjpeg"]
        elif self.camara.formato == "yuyv422":
            seleccion_formato = ["-pixel_format", "yuyv422"]

        return [
            "-f", "dshow",
            "-rtbufsize", "256M",          # colchon ante microcortes USB
            *seleccion_formato,
            "-video_size", v["resolucion"],
            "-framerate", str(v["fps"]),
            "-i", especificador,
        ]

    def comando(self, destino: Path) -> list[str]:
        """Construye la linea de FFmpeg completa para esta camara."""
        v = self.cfg["video"]
        cmd = [
            # OJO: nada de -nostdin. Necesitamos el stdin abierto para enviar
            # 'q' al parar; es lo unico que hace que FFmpeg cierre el MKV
            # escribiendo la duracion. Ver detener().
            "ffmpeg", "-hide_banner", "-loglevel", "error",

            # -progress pipe:2 emite el avance por stderr en formato clave=valor.
            # De ahi salen los frames que vigila _leer_progreso().
            "-progress", "pipe:2", "-stats_period", "0.5",

            *self._entrada(),               # testsrc2 o capturadora, segun config

            "-c:v", "libx264",
            "-preset", v["preset"],
            "-crf", str(v["crf"]),
            "-pix_fmt", "yuv420p",          # compatibilidad de reproduccion
            "-g", str(v["fps"] * 2),        # keyframe cada 2s, facilita recortes
        ]

        if self.camara.con_audio:
            cmd += [
                "-c:a", "aac", "-b:a", "160k",
                # El reloj del audio HDMI puede ir a distinta velocidad que el
                # del video; sin resampleo asincrono, la sincronia deriva a lo
                # largo de un asalto largo. aresample=async=1 lo compensa.
                "-af", "aresample=async=1",
            ]
        else:
            cmd.append("-an")               # sin audio (modo prueba o sin micro)

        # El contenedor lo decide la extension .mkv del destino: elegido porque
        # sobrevive a un corte de luz. Un MP4 quedaria inservible.
        cmd += ["-y", str(destino)]
        return cmd

    @property
    def puede_relanzarse(self) -> bool:
        """True si esta camara esta caida y se puede volver a lanzar.

        Solo tiene sentido con el asalto en curso: la camara ya no graba (el
        proceso murio o fallo al arrancar) pero las demas siguen. No se exige
        que haya error registrado porque una camara puede terminar sin decir
        nada y seguir siendo un fallo.
        """
        return self.carpeta is not None and not self.estado.grabando

    def _siguiente_libre(self, carpeta: Path) -> Path:
        """Primer nombre de trozo sin usar: cam1_b.mkv, cam1_c.mkv...

        Se busca por el fichero en disco y no por len(self.trozos) porque un
        intento fallido no deja fichero ni cuenta como trozo: si se numerara por
        la lista, el siguiente relanzamiento podria pisar un trozo bueno.
        """
        for letra in "bcdefghijklmnopqrstuvwxyz":
            candidato = carpeta / f"{self.camara.id}_{letra}.mkv"
            if not candidato.exists():
                return candidato
        # 25 relanzamientos en un asalto: inalcanzable en la practica, pero mejor
        # sobrescribir el ultimo que quedarse sin nombre y no poder grabar.
        return carpeta / f"{self.camara.id}_z.mkv"

    def _segmento_negro(self, carpeta: Path, indice: int, hueco: float,
                        muestra_audio: int | None = None) -> Path | None:
        """Crea un trozo negro temporal para representar un hueco de relanzamiento.

        'hueco' va en SEGUNDOS y es lo que durara el negro. 'indice' solo entra
        en el nombre del fichero temporal ('_cam1_gap_1.mkv'), para que dos
        huecos de la misma camara no se pisen. 'muestra_audio' es la frecuencia
        en Hz que debe tener el silencio: si es None se usa 44100, pero conviene
        pasar la del trozo real (ver ffmpeg_utils.muestra_audio) para que el
        demuxer concat no tenga que mezclar frecuencias distintas.

        Devuelve la ruta del temporal, o None si no se pudo generar; quien llama
        debe tratar ese None (ver el plan B de _unir_trozos).

        Se usa solo al unir: el trozo resultante lleva video negro real (y audio
        en silencio si la camara tenia micro), de modo que el fichero final no
        depende de saltos de timestamps para mostrar el tramo caido.
        """
        v = self.cfg["video"]
        muestra_audio = muestra_audio or 44100
        destino = carpeta / f"_{self.camara.id}_gap_{indice}.mkv"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s={v['resolucion']}:r={v['fps']}",
        ]
        if self.camara.con_audio:
            cmd += [
                "-f", "lavfi",
                "-i", f"anullsrc=channel_layout=stereo:sample_rate={muestra_audio}",
            ]
        cmd += [
            "-t", f"{hueco:.3f}",
            "-map", "0:v:0",
            "-c:v", "libx264",
            "-preset", v["preset"],
            "-crf", str(v["crf"]),
            "-pix_fmt", "yuv420p",
            "-g", str(v["fps"] * 2),
        ]
        if self.camara.con_audio:
            cmd += [
                "-map", "1:a:0",
                "-c:a", "aac",
                "-b:a", "160k",
            ]
        else:
            cmd.append("-an")
        cmd.append(str(destino))

        try:
            r = subprocess.run(
                cmd,
                capture_output=True, text=True, errors="replace",
                timeout=60, creationflags=_SIN_VENTANA,
            )
            if r.returncode == 0 and destino.exists() and destino.stat().st_size > 0:
                return destino
        except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
            pass
        destino.unlink(missing_ok=True)
        return None

    def iniciar(self, carpeta: Path, relanzamiento: bool = False) -> None:
        """Lanza FFmpeg y el hilo que vigila su salida. No bloquea.

        'carpeta' es la del asalto, ya creada: aqui dentro se escribe el .mkv de
        esta camara.

        'relanzamiento' distingue el arranque normal del asalto de volver a
        lanzar una camara caida, y cambia bastante lo que pasa: en el arranque
        normal se reinician trozos, huecos y contador de frames; al relanzar se
        conservan, se anota el hueco de la caida y se escribe en un fichero
        libre (cam1_b.mkv, cam1_c.mkv...) para no pisar lo ya grabado. Al
        detener el asalto se concatenan en un unico cam1.mkv (ver
        Sesion._unir_trozos).
        """
        self.carpeta = carpeta
        if not relanzamiento:
            self.trozos = []
            self.huecos = []
            self.frames_previos = 0
            destino = carpeta / f"{self.camara.id}.mkv"
        else:
            # Se conservan los frames de los trozos anteriores para que el
            # contador de la interfaz siga subiendo en vez de volver a cero.
            self.frames_previos = self.estado.frames
            # Tiempo que la camara ha estado realmente sin imagen nueva.
            # Si FFmpeg tarda en caer despues de congelarse la captura, el
            # ultimo frame valido manda mas que el cierre del proceso; asi el
            # hueco negro cubre tambien esos segundos "atascados".
            ultimo_evento = self.estado.ultimo_avance
            if self._fin_trozo is not None:
                ultimo_evento = min(ultimo_evento, self._fin_trozo)
            hueco = max(0.0, time.monotonic() - ultimo_evento)
            # Se anota AQUI, alineado con el trozo que esta a punto de crearse.
            # Hubo una version que lo dejaba apuntado para que lo recogiera
            # _leer_progreso() al cerrar el trozo anterior, pero ese trozo ya
            # estaba cerrado: el valor se perdia, 'huecos' quedaba en [0.0] y no
            # se insertaba ningun negro.
            while len(self.huecos) < len(self.trozos):
                self.huecos.append(0.0)
            self.huecos.append(hueco)
            destino = self._siguiente_libre(carpeta)

        # Estado nuevo en cada arranque: arrastrar el anterior mostraria en la
        # interfaz frames o errores del asalto (o del intento) ya terminado.
        self.estado = EstadoCamara(grabando=True, fichero=destino)
        self._ultimas_lineas = []
        self._detencion_pedida = False

        try:
            self._proc = subprocess.Popen(
                self.comando(destino),
                stderr=subprocess.PIPE,      # por aqui llegan progreso y errores
                stdout=subprocess.DEVNULL,   # el video va a fichero, no a stdout
                stdin=subprocess.PIPE,       # imprescindible para enviar 'q'
                text=True,
                errors="replace",            # nombres de camara con acentos
                bufsize=1,                   # linea a linea: progreso al momento
                creationflags=_SIN_VENTANA,
            )
        except FileNotFoundError:
            # FFmpeg no esta instalado o no esta en el PATH. Se refleja como
            # error de la camara para que salte en la interfaz igual que
            # cualquier otro fallo, en vez de tumbar la aplicacion.
            self.estado.grabando = False
            self.estado.error = "FFmpeg no encontrado en el PATH"
            return

        # OJO: el trozo NO se anota aqui. Que Popen no falle solo significa que
        # el ejecutable existe; FFmpeg puede morir un instante despues sin haber
        # creado el fichero (p.ej. 'I/O error' al abrir una capturadora con un
        # modo inexistente). Anotarlo aqui inflaba el contador de intentos con
        # arranques que no grabaron nada. Se anota al final de _leer_progreso(),
        # cuando el proceso ya ha muerto y se sabe si dejo fichero con contenido.

        # daemon=True: si la aplicacion se cierra de golpe, estos hilos no
        # impiden que el proceso Python termine.
        self._hilo = threading.Thread(target=self._leer_progreso, daemon=True)
        self._hilo.start()

    def _leer_progreso(self) -> None:
        """Lee la salida de FFmpeg para detectar avance y errores.

        Corre en su propio hilo durante toda la grabacion. El bucle termina
        solo cuando FFmpeg cierra stderr, es decir, cuando el proceso muere.

        Cada linea cae en una de tres categorias:
          'frame=N'   -> avance: actualiza contador y marca de tiempo
          telemetria  -> se descarta (bitrate, speed, out_time...)
          resto       -> error potencial: se guardan las ultimas 5
        """
        assert self._proc and self._proc.stderr
        for linea in self._proc.stderr:
            linea = linea.strip()
            if not linea:
                continue

            if m := self._RE_FRAME.search(linea):
                # Se suman los trozos anteriores: tras un relanzamiento el
                # contador de la interfaz debe seguir subiendo, no reiniciarse.
                self.estado.frames = self.frames_previos + int(m.group(1))
                # Esta marca es la que permite detectar la imagen congelada:
                # si deja de refrescarse, EstadoCamara.bloqueada se activa.
                self.estado.ultimo_avance = time.monotonic()
            elif not linea.startswith(self._TELEMETRIA) and not self._es_ruido(linea):
                # Lo que no es telemetria ni ruido conocido, es un error real.
                # Se conservan solo las ultimas 5 lineas: si FFmpeg entra en
                # bucle de errores, la lista no crece sin limite.
                self._ultimas_lineas.append(linea)
                del self._ultimas_lineas[:-5]

        # --- A partir de aqui el proceso ya ha terminado ---
        codigo = self._proc.wait()
        self.estado.grabando = False

        # Se cuenta como trozo solo si dejo fichero con contenido. Un arranque
        # fallido ('I/O error' al abrir la capturadora) no deja nada y no debe
        # contar como intento grabado ni entrar en la concatenacion.
        fichero = self.estado.fichero
        if fichero and fichero.exists() and fichero.stat().st_size > 0:
            if fichero not in self.trozos:
                self.trozos.append(fichero)
                # El hueco previo a este trozo ya lo anoto iniciar(); aqui solo
                # se rellena si faltara (primer trozo: sin hueco por delante).
                while len(self.huecos) < len(self.trozos):
                    self.huecos.append(0.0)
        # Instante de la muerte: si el operador relanza, la distancia hasta ese
        # momento es el tiempo que la camara estuvo sin grabar.
        self._fin_trozo = time.monotonic()

        # El codigo de salida por si solo NO sirve para decidir si hubo fallo:
        # al pararlo nosotros, FFmpeg puede devolver 1 o 255 con la grabacion
        # perfectamente correcta. De ahi que se distinga por que murio.
        if self._detencion_pedida:
            # Parada nuestra: solo es fallo si ademas informo de algo raro.
            if self._ultimas_lineas and not self.estado.error:
                self.estado.error = self._ultimas_lineas[-1][:120]
        elif codigo != 0 and not self.estado.error:
            # Murio por su cuenta a mitad del asalto: siempre es un fallo.
            detalle = self._ultimas_lineas[-1] if self._ultimas_lineas else f"codigo {codigo}"
            self.estado.error = detalle[:120]

    def pedir_parada(self) -> None:
        """Envia la 'q' a FFmpeg sin esperar a que termine.

        Separado de esperar_cierre() para que Sesion pueda cortar las tres
        camaras casi a la vez. Cuando se hacia todo seguido, cada camara seguia
        grabando mientras la anterior cerraba (hasta 8 s cada una) y los tres
        POV salian con duraciones muy dispares: 26 / 29 / 33 s en un caso real.
        La 'q' es lo que fija el instante de corte, asi que enviarlas juntas
        deja las duraciones a menos de un segundo.
        """
        # Se marca ANTES de tocar el proceso: el hilo lector puede despertarse
        # en cuanto FFmpeg muera, y necesita saber que la parada es nuestra.
        self._detencion_pedida = True
        if not self._proc or self._proc.poll() is not None:
            return  # ya habia terminado (probablemente por un fallo)

        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.write("q")
                self._proc.stdin.flush()
                self._proc.stdin.close()
        except (OSError, ValueError):
            # El pipe ya estaba roto o cerrado: FFmpeg habia muerto justo antes
            # de escribir la 'q'. esperar_cierre() se encarga del resto.
            pass

    def esperar_cierre(self) -> None:
        """Espera a que FFmpeg acabe de escribir el fichero.

        Se llama despues de pedir_parada(). Como los tres esperan en paralelo
        (ya tienen su 'q' enviada), el timeout de 8 s es del conjunto, no por
        camara.

        Cascada de dos respaldos si no cierra solo:
            terminate() + 5 s  -> fichero valido, sin duracion
            kill()             -> ultimo recurso
        """
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()

        # Esperar al hilo lector garantiza que estado.error ya esta escrito
        # cuando Sesion.detener_asalto() vaya a componer el metadata.json.
        if self._hilo:
            self._hilo.join(timeout=3)


class Sesion:
    """Coordina las tres camaras y la numeracion de asaltos.

    Es el objeto que maneja la interfaz: iniciar_asalto() / detener_asalto()
    y la propiedad grabando. Solo admite un asalto a la vez.
    """

    def __init__(self, ruta_cfg: Path):
        """'ruta_cfg' es el config.json, que se lee AQUI y se reescribe despues.

        De el salen las camaras y la carpeta de grabaciones (que se crea si no
        existe). Los cambios posteriores (dispositivos, contador, ajustes del
        mosaico) se persisten en ese mismo fichero releyendolo antes de escribir,
        para no pisar lo que se haya tocado por fuera: ver _actualizar_config().
        """
        self.ruta_cfg = ruta_cfg
        self.cfg = json.loads(ruta_cfg.read_text(encoding="utf-8"))
        self.camaras = [Camara(**c) for c in self.cfg["camaras"]]
        self.raiz = Path(self.cfg["carpeta_grabaciones"])
        self.raiz.mkdir(parents=True, exist_ok=True)

        self.grabadores: list[GrabadorCamara] = []
        self.asalto_actual: dict | None = None

    # Dias de la semana en mayusculas, indexados por datetime.weekday().
    _DIAS = ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO")

    # Carpetas que cuentan como jornada (MIERCOLES_22).
    _RE_JORNADA = re.compile(rf"^(?:{'|'.join(_DIAS)})_\d{{2}}$")

    # Carpeta de asalto: el ID va al FINAL, tras el marcador literal '_ID_'
    # (12_47_ID_027). El marcador evita la ambiguedad que habria entre campos
    # numericos separados por '_': con los tiradores delante, '12_47_027' no
    # permite saber cual de los tres numeros es el asalto. Se usa para detectar
    # colisiones al crear la carpeta, no para numerar (ver siguiente_numero).
    _RE_ASALTO = re.compile(r"(?:^|_)ID_(\d{3})$")

    @classmethod
    def carpeta_dia(cls, momento: datetime | None = None) -> str:
        """Nombre de la carpeta de la jornada: MIERCOLES_22."""
        momento = momento or datetime.now()
        return f"{cls._DIAS[momento.weekday()]}_{momento.day:02d}"

    def siguiente_numero(self) -> int:
        """Numero que se asignara al proximo asalto.

        El contador vive UNICAMENTE en config.json ('ultimo_asalto'). No se
        contrasta con las carpetas del disco: config.json es el punto unico de
        control, de modo que reiniciar o corregir la numeracion es editar un
        numero en un fichero y nada mas. Renombrar o mover carpetas no afecta.

        Contrapartida asumida: si config.json se pierde o se restaura una copia
        antigua, el contador retrocede y el proximo asalto reutilizaria un
        numero ya usado. Contra eso protege iniciar_asalto(), que se niega a
        escribir dentro de una carpeta que ya contiene una grabacion y busca el
        siguiente numero libre. Es decir: el contador decide, pero nunca puede
        destruir material grabado.
        """
        return int(self.cfg.get("ultimo_asalto", 0)) + 1

    def _carpeta_ocupada(self, jornada: Path, numero: int) -> bool:
        """True si ya hay una grabacion con ese numero en esa jornada.

        Se busca por el ID del final del nombre ('..._ID_027'), no por el nombre
        completo, porque los tiradores del nuevo asalto no tienen por que
        coincidir con los del que ya existe: '12_47_ID_027' y '3_9_ID_027' son
        el mismo numero de asalto y colisionarian igual.

        Solo cuenta como ocupada si hay metadata.json: lo escribe esta
        aplicacion al terminar de grabar, asi que su presencia significa que ahi
        dentro hay material. Una carpeta vacia (creada y abandonada por un
        arranque fallido) no bloquea el numero.
        """
        if not jornada.exists():
            return False
        for carpeta in jornada.iterdir():
            if not carpeta.is_dir() or not (carpeta / "metadata.json").exists():
                continue
            if m := self._RE_ASALTO.search(carpeta.name):
                if int(m.group(1)) == numero:
                    return True
        return False

    def _actualizar_config(self, cambios: dict) -> None:
        """Aplica 'cambios' a config.json releyendolo antes de escribir.

        Releer evita pisar otros ajustes que se hayan tocado (a mano o desde
        otra parte de la app) mientras esta instancia estaba viva. Los cambios
        se reflejan tambien en self.cfg para no tener que recargar la sesion.
        """
        try:
            datos = json.loads(self.ruta_cfg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            datos = dict(self.cfg)
        datos.update(cambios)
        self.cfg.update(cambios)
        try:
            self.ruta_cfg.write_text(
                json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            # Si no se puede escribir (fichero bloqueado, disco lleno), no se
            # interrumpe la grabacion: perder un ajuste es menos grave que no
            # grabar. Para el contador significa que el numero no queda
            # reservado y el proximo asalto volveria a calcularlo igual; de que
            # no pise lo ya grabado se encarga la comprobacion de colision de
            # iniciar_asalto().
            pass

    def _guardar_contador(self, numero: int) -> None:
        """Anota en config.json el ultimo numero de asalto usado."""
        self._actualizar_config({"ultimo_asalto": numero})

    @property
    def frontal_mosaico(self) -> str:
        """Id de la camara que va grande arriba en el mosaico.

        Por defecto la del medio en config.json, que en el reparto habitual
        [izq, frontal, der] es la frontal. Si el id guardado ya no existe entre
        las camaras, se vuelve a esa por defecto.
        """
        guardado = self.cfg.get("frontal_mosaico")
        if guardado and any(c.id == guardado for c in self.camaras):
            return guardado
        medio = len(self.camaras) // 2
        return self.camaras[medio].id if self.camaras else "cam2"

    def guardar_frontal_mosaico(self, cam_id: str) -> None:
        """Fija que camara va arriba en el mosaico y lo persiste."""
        self._actualizar_config({"frontal_mosaico": cam_id})

    @property
    def audio_mosaico(self) -> str:
        """Id de la camara de la que el mosaico toma el audio ('cam2' por defecto).

        Se guarda el id y no el nombre de fichero para que siga valiendo si
        cambian los nombres. Si el guardado ya no existe entre las camaras, se
        cae a la frontal (la del medio), que es el reparto habitual.
        """
        guardado = self.cfg.get("audio_mosaico")
        if guardado and any(c.id == guardado for c in self.camaras):
            return guardado
        medio = len(self.camaras) // 2
        return self.camaras[medio].id if self.camaras else "cam2"

    def guardar_audio_mosaico(self, cam_id: str) -> None:
        """Fija de que camara toma el audio el mosaico y lo persiste."""
        self._actualizar_config({"audio_mosaico": cam_id})

    def _persistir_camaras(self) -> None:
        """Vuelca el estado de los objetos Camara vivos a config.json.

        Los Camara son la fuente de verdad: el dict de config se reconstruye a
        partir de ellos, no al reves. Asi video y audio nunca se desincronizan.
        """
        camaras = [
            {"id": c.id, "nombre": c.nombre, "dispositivo": c.dispositivo,
             "dispositivo_nombre": c.dispositivo_nombre,
             "audio": c.audio, "formato": c.formato}
            for c in self.camaras
        ]
        self._actualizar_config({
            "camaras": camaras,
            "modo_prueba": not any(c.configurada for c in self.camaras),
        })

    def asignar_video(self, cam_id: str, dispositivo: dispositivos.Dispositivo | None,
                      audios: list[dispositivos.Dispositivo] | None = None) -> None:
        """Asigna el dispositivo de video de una camara y lo persiste.

        'dispositivo' es un Dispositivo (id de hardware + nombre) o None para
        volver la camara a modo prueba. Al asignar video se autoempareja su
        micro; si se quita el video, tambien se quita el audio (no tiene sentido
        grabar solo el micro). La eleccion manual de micro va en guardar_audio().

        'audios' es la lista de micros ya enumerada, que se pasa para no volver
        a preguntar a FFmpeg (bloquea un instante la interfaz). Si es None se
        enumera aqui, comodo desde un script pero peor desde la GUI, que ya la
        tiene en memoria.

        Si el micro que sale del emparejado ya lo tiene otra camara, se deja sin
        audio en vez de duplicarlo: con capturadoras identicas el emparejado va
        por nombre y puede devolver el mismo micro para dos camaras. Mejor una
        camara muda (visible en la interfaz, se corrige a mano) que dos apuntando
        al mismo micro, que en directo pasa desapercibido.
        """
        if audios is None:
            audios = dispositivos.listar_audio()

        for cam in self.camaras:
            if cam.id != cam_id:
                continue
            if dispositivo:
                cam.dispositivo = dispositivo.id
                cam.dispositivo_nombre = dispositivo.nombre
                micro = dispositivos.emparejar_audio(dispositivo, audios)
                ocupados = {c.audio for c in self.camaras
                            if c.id != cam_id and c.audio}
                cam.audio = micro.id if micro and micro.id not in ocupados else None
            else:
                cam.dispositivo = None
                cam.dispositivo_nombre = None
                cam.audio = None
        self._persistir_camaras()

    def guardar_audio(self, cam_id: str, audio: str | None) -> None:
        """Fija manualmente el micro de una camara (id/nombre, o None sin audio)."""
        for cam in self.camaras:
            if cam.id == cam_id:
                cam.audio = audio
        self._persistir_camaras()

    def guardar_formato(self, cam_id: str, formato: str) -> None:
        """Fija el formato de entrada de una camara: mjpeg | yuyv422 | auto."""
        for cam in self.camaras:
            if cam.id == cam_id:
                cam.formato = formato
        self._persistir_camaras()

    @staticmethod
    def _limpiar(texto: str) -> str:
        """Convierte el texto de tiradores en un nombre de carpeta valido.

        El operador escribe libremente ("12 47", o "Garcia vs Lopez") y eso
        acaba siendo un nombre de carpeta que ademas viaja a Dropbox.

        Con flags=UNICODE, \\w conserva letras acentuadas y enes: se quitan los
        signos problematicos (/ \\ : * ? " < > |) pero no se destroza el nombre.
        Los separadores (espacios y guiones) pasan a '_', de modo que los dos
        numeros de tirador salen ya como '12_47' sin pedirle al operador ningun
        formato concreto. Se corta a 60 caracteres para no acercarse al limite
        de ruta de Windows.

        No se valida que sean numeros a proposito: en directo, un campo que
        rechaza lo que se teclea es un obstaculo, y el ID final identifica el
        asalto pase lo que pase en esta parte del nombre.
        """
        limpio = re.sub(r"[^\w\s-]", "", texto, flags=re.UNICODE).strip()
        return re.sub(r"[\s-]+", "_", limpio)[:60].strip("_")

    @property
    def grabando(self) -> bool:
        return self.asalto_actual is not None

    def iniciar_asalto(self, etiqueta: str = "") -> dict:
        """Crea la carpeta del asalto y arranca las tres camaras.

        'etiqueta' es texto libre del operador (normalmente los dos numeros de
        tirador: "12 47"). Se limpia con _limpiar() y encabeza el nombre de la
        carpeta; vacia deja solo el ID. No se valida a proposito: en directo un
        campo que rechaza lo que se teclea es un obstaculo.

        Devuelve el dict del asalto en curso (numero, carpeta, jornada, inicio).
        Vuelve enseguida: las camaras siguen grabando en segundo plano y su
        estado se consulta a traves de self.grabadores[i].estado.
        """
        if self.grabando:
            raise RuntimeError("Ya hay un asalto en curso")

        inicio = datetime.now()
        numero = previsto = self.siguiente_numero()
        jornada = self.carpeta_dia(inicio)

        # Red de seguridad del punto unico de control: si config.json se
        # restauro atrasado, el numero calculado puede estar ya grabado. Se
        # avanza hasta el primer libre en vez de escribir encima (mkdir con
        # exist_ok=True no falla: grabaria dentro y pisaria los .mkv). El limite
        # solo evita un bucle infinito si algo va muy mal.
        #
        # Se conserva 'previsto' para que la interfaz pueda avisar del salto:
        # una numeracion que da un brinco sin explicacion parece un fallo de la
        # aplicacion, cuando lo que hay es un config.json desfasado.
        raiz_jornada = self.raiz / jornada
        for _ in range(1000):
            if not self._carpeta_ocupada(raiz_jornada, numero):
                break
            numero += 1

        # TIRADOR1_TIRADOR2_ID_NNN, dentro de la carpeta de la jornada:
        # MIERCOLES_22/12_47_ID_027. El ID va al final para que no se confunda
        # con los numeros de tirador que escribe el operador; el marcador '_ID_'
        # lo delimita sin ambiguedad. Sin tiradores queda solo ID_027.
        sufijo = self._limpiar(etiqueta)
        nombre = (f"{sufijo}_" if sufijo else "") + f"ID_{numero:03d}"
        carpeta = raiz_jornada / nombre
        carpeta.mkdir(parents=True, exist_ok=True)

        # El contador se guarda al iniciar, no al terminar: si la aplicacion
        # muere durante el asalto, el numero ya esta reservado y no se reutiliza.
        self._guardar_contador(numero)

        # Grabadores nuevos en cada asalto: cada uno lleva su propio proceso
        # y su propio hilo, y no se reutilizan una vez terminados.
        self.grabadores = [GrabadorCamara(c, self.cfg) for c in self.camaras]
        for g in self.grabadores:
            g.iniciar(carpeta)  # arranque secuencial: de ahi el desfase de ~1 s

        self.asalto_actual = {
            "numero": numero,
            "numero_previsto": previsto,   # != numero si hubo colision
            "etiqueta": etiqueta,
            "carpeta": carpeta,
            "jornada": jornada,
            "inicio": inicio,
        }
        return self.asalto_actual

    def relanzar_camara(self, cam_id: str) -> bool:
        """Vuelve a lanzar una camara caida sin interrumpir el asalto.

        Pensado para la caida en directo: se afloja un USB, esa camara muere y
        las otras dos siguen. Antes habia que detener el asalto entero; asi se
        recupera la camara y se pierde solo el hueco hasta que el operador pulsa.

        Lo ya grabado NO se sobrescribe: el nuevo intento escribe un fichero
        aparte (cam1_b.mkv) y al detener el asalto se concatenan todos en un
        unico cam1.mkv. Devuelve False si no hay asalto, la camara no existe o
        sigue grabando (nada que relanzar).
        """
        if not self.asalto_actual:
            return False
        for g in self.grabadores:
            if g.camara.id != cam_id:
                continue
            if not g.puede_relanzarse:
                return False
            g.iniciar(self.asalto_actual["carpeta"], relanzamiento=True)
            # Margen para que un arranque fallido se manifieste. FFmpeg tarda
            # unas decimas en abortar con 'I/O error' (capturadora ausente o
            # modo inexistente); sin esta espera se informaria de un
            # relanzamiento correcto que en realidad murio al instante.
            time.sleep(1.2)
            return g.estado.grabando
        return False

    def _duracion_referencia(self) -> float:
        """Cuanto duro el asalto en video, segun las camaras que NO se cayeron.

        Se toma la mayor duracion entre las camaras de un solo trozo. Es la
        medida fiable de lo que duro el asalto: sale del propio contenedor, no
        de un reloj.

        Por que hace falta: el hueco medido con time.monotonic() (entre que
        muere el proceso y se pulsa Relanzar) se queda CORTO frente al tiempo
        real perdido. La capturadora deja de dar imagen antes de que FFmpeg
        muera, y el FFmpeg nuevo tarda en abrir el dispositivo y escribir su
        primer frame. Con hardware real eso son varios segundos que el
        cronometro no ve, y el video reanudado quedaba adelantado respecto a las
        otras camaras: el mosaico terminaba antes de tiempo (`shortest=1` corta
        con la mas corta) y las demas nunca llegaban a su final.

        Devuelve 0.0 si todas las camaras se cayeron: entonces no hay referencia
        y _unir_trozos() se queda con lo medido por reloj, que es mejor que nada.

        Solo cuentan las camaras de UN trozo: son las que grabaron el asalto de
        principio a fin y por tanto miden lo que duro de verdad. Una camara con
        varios trozos aun no esta unida y su duracion no significa nada todavia.
        """
        duraciones = [
            _duracion(g.trozos[0])
            for g in self.grabadores
            if len(g.trozos) == 1 and g.trozos[0].exists()
        ]
        return max(duraciones, default=0.0)

    @staticmethod
    def _unir_trozos(g: GrabadorCamara, referencia: float = 0.0) -> None:
        """Concatena en un solo fichero los trozos de una camara relanzada.

        Solo actua si hubo relanzamiento (mas de un trozo). Se usa el demuxer
        'concat' con -c copy: no recodifica, asi que es instantaneo y sin
        perdida. Los trozos comparten codec, resolucion y timebase por venir del
        mismo comando(), que es lo que exige el demuxer.

        El resultado se escribe aparte y solo sustituye al primer trozo si
        FFmpeg termina bien: si la union falla, se conservan los trozos sueltos
        intactos. Nunca se destruye material grabado por un fallo al unir.

        EL HUECO SE CONVIERTE EN NEGRO REAL. Entre trozo y trozo se genera un
        segmento temporal negro con los mismos parametros de video, y audio en
        silencio si la camara lo llevaba. Asi el fichero final muestra el tramo
        caido como negro visible, pero sigue durando lo mismo que el asalto y
        conserva la sincronia con las demas camaras.

        EL NEGRO SE MIDE CONTRA LAS OTRAS CAMARAS, no con el cronometro.
        'referencia' es lo que duro la camara que no se cayo; el negro necesario
        es esa duracion menos lo que esta camara grabo de verdad. El reloj de
        time.monotonic() se queda corto (no ve lo que tarda la capturadora en
        morir ni el FFmpeg nuevo en arrancar) y el video reanudado salia
        ADELANTADO: en el mosaico, cam1 iba unos segundos por delante de las
        otras y el conjunto terminaba antes de tiempo. Con varios trozos, el
        deficit se reparte proporcionalmente a lo medido por reloj.
        """
        existentes = [t for t in g.trozos if t.exists() and t.stat().st_size > 0]
        if len(existentes) < 2:
            return

        carpeta = existentes[0].parent
        lista = carpeta / f"_concat_{g.camara.id}.txt"
        unido = carpeta / f"{g.camara.id}_unido.mkv"
        temporales: list[Path] = []
        try:
            muestra_audio = _muestra_audio(existentes[0])

            # Huecos definitivos. Se parte de lo medido por reloj y, si hay
            # referencia, se escala para que el total (video + negro) cuadre con
            # lo que duraron las camaras sanas.
            medidos = [g.huecos[i] if i < len(g.huecos) else 0.0
                       for i in range(len(existentes))]
            grabado = sum(_duracion(t) for t in existentes)
            huecos = list(medidos)
            if referencia > 0:
                falta = referencia - grabado
                suma_medida = sum(medidos[1:])
                if falta > 0.1 and suma_medida > 0.1:
                    # Reparto proporcional: respeta donde ocurrio cada caida.
                    escala = falta / suma_medida
                    huecos = [medidos[0]] + [h * escala for h in medidos[1:]]
                elif falta > 0.1:
                    # Sin medida previa util (o una sola caida): todo al primer
                    # hueco, que es donde se sabe que estuvo la interrupcion.
                    huecos = list(medidos)
                    if len(huecos) > 1:
                        huecos[1] = falta

            # Lista del demuxer concat. Cada hueco se materializa como un
            # fichero negro temporal con la misma estructura de streams, para
            # que el resultado final no dependa de timestamps vacios.
            filas = []
            for i, t in enumerate(existentes):
                filas.append(f"file '{t.name}'")
                hueco = huecos[i + 1] if i + 1 < len(huecos) else 0.0
                if hueco > 0.1:
                    negro = g._segmento_negro(carpeta, i + 1, hueco, muestra_audio)
                    if negro:
                        temporales.append(negro)
                        filas.append(f"file '{negro.name}'")
                    else:
                        # Plan B: si no se pudo generar el negro, al menos se
                        # mantiene la sincronia temporal con la directiva.
                        real = _duracion(t)
                        if real > 0:
                            filas.append(f"duration {real + hueco:.3f}")
            lista.write_text("\n".join(filas) + "\n", encoding="utf-8")
            r = subprocess.run(
                # -fflags +genpts regenera los timestamps que falten. Con trozos
                # bien cerrados la union ya sale limpia sin el; ayuda cuando el
                # trozo anterior quedo a medias (proceso muerto sin cerrar el
                # MKV, que es cuando aparecen avisos de dts no monotono).
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-fflags", "+genpts",
                 "-f", "concat", "-safe", "0", "-i", str(lista),
                 "-c", "copy", str(unido)],
                capture_output=True, text=True, errors="replace",
                timeout=120, creationflags=_SIN_VENTANA,
            )
            if r.returncode == 0 and unido.exists() and unido.stat().st_size > 0:
                for t in existentes:
                    t.unlink(missing_ok=True)
                unido.rename(existentes[0])   # el unido pasa a ser cam1.mkv
                g.estado.fichero = existentes[0]
            else:
                unido.unlink(missing_ok=True)  # union fallida: quedan los trozos
        except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
            # Cualquier fallo aqui deja los trozos sueltos, que son validos.
            pass
        finally:
            for t in temporales:
                t.unlink(missing_ok=True)
            lista.unlink(missing_ok=True)

    def detener_asalto(self) -> dict:
        """Para las tres camaras y deja escrito metadata.json.

        Devuelve la metadata para que la interfaz avise si alguna camara fallo.
        """
        if not self.asalto_actual:
            raise RuntimeError("No hay ningun asalto en curso")

        # En DOS pasadas, no una. Primero la 'q' a los tres seguida (es lo que
        # fija el instante de corte), y solo despues se espera a que cierren.
        # Haciendolo camara a camara, cada una seguia grabando mientras la
        # anterior cerraba y las duraciones salian dispares (26/29/33 s reales);
        # asi quedan a menos de un segundo.
        for g in self.grabadores:
            g.pedir_parada()
        # Bloqueante a proposito: garantiza que los tres ficheros estan cerrados
        # y completos antes de componer la metadata.
        for g in self.grabadores:
            g.esperar_cierre()

        # Con los ficheros ya cerrados, se unen los trozos de las camaras que se
        # relanzaron. Es -c copy (instantaneo) y solo afecta a las que tengan
        # mas de un trozo; el caso normal no paga nada.
        #
        # La referencia es cuanto grabo la camara que MAS duro sin caerse: es la
        # medida exacta de lo que duro el asalto en video, y con ella se calcula
        # el negro que le falta a cada camara caida. Ver _unir_trozos().
        referencia = self._duracion_referencia()
        for g in self.grabadores:
            self._unir_trozos(g, referencia)

        info = self.asalto_actual
        fin = datetime.now()
        duracion = (fin - info["inicio"]).total_seconds()

        # metadata.json acompana a los videos hasta Dropbox: es el registro de
        # que se grabo, cuanto duro y si hubo incidencias en alguna camara.
        metadata = {
            "competicion": self.cfg["competicion"],
            "jornada": info["jornada"],
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
                    "dispositivo": g.camara.dispositivo_nombre,  # nombre legible
                    "audio": g.camara.audio,   # micro usado, o null si sin audio
                    "formato": g.camara.formato if g.camara.configurada else None,
                    "fichero": g.estado.fichero.name if g.estado.fichero else None,
                    "frames": g.estado.frames,
                    # >1 si la camara se cayo y el operador la relanzo. El video
                    # resultante conserva la sincronia (el hueco se declara en la
                    # linea de tiempo), pero ese tramo esta en negro.
                    "intentos": len(g.trozos),
                    # Segundos totales que la camara estuvo caida, 0 si ninguno.
                    "segundos_caida": round(sum(g.huecos), 1),
                    # Un tamano de 0 delata una camara que no llego a grabar
                    # aunque no informara de ningun error.
                    "tamano_bytes": (
                        g.estado.fichero.stat().st_size
                        if g.estado.fichero and g.estado.fichero.exists() else 0
                    ),
                    "error": g.estado.error,
                }
                for g in self.grabadores
            ],
        }
        # ensure_ascii=False para que los nombres con tildes se lean tal cual.
        (info["carpeta"] / "metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # La carpeta se anade al dict devuelto (no al JSON: es una ruta local que
        # no tiene sentido subir a Dropbox). La usa la app para el mosaico.
        resultado = dict(metadata, carpeta=str(info["carpeta"]))

        # Sesion queda libre para el siguiente asalto.
        self.asalto_actual = None
        self.grabadores = []
        return resultado
