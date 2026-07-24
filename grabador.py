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

# En Windows, evita que se abra una ventana de consola por cada FFmpeg.
_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


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
        return (time.monotonic() - self.ultimo_avance) > 5.0


class GrabadorCamara:
    """Envuelve un proceso FFmpeg y sigue su progreso.

    Se lanza un proceso por camara en lugar de uno solo con tres entradas.
    Cuesta mas codigo, pero un unico proceso seria un unico punto de fallo:
    al desconectarse una capturadora, FFmpeg abortaria y se perderian los tres
    POVs del asalto en vez de uno. En un evento irrepetible eso lo justifica.

    El precio es que los tres arrancan escalonados (~1 s). Irrelevante para
    revision tecnica; para montaje sincronizado al frame haria falta claqueta.
    """

    _RE_FRAME = re.compile(r"frame=\s*(\d+)")

    # Prefijos de las lineas de telemetria de -progress.
    _TELEMETRIA = ("bitrate=", "total_size=", "out_time", "speed=", "fps=",
                   "dup_frames=", "drop_frames=", "progress=", "stream_")

    # Avisos benignos de FFmpeg que no deben marcarse como fallo de camara:
    # generarian falsas alarmas en mitad de la competicion.
    #
    # Al anadir entradas aqui, comprobar que no tapan un fallo real: una camara
    # marcada como correcta cuando no graba es peor que una falsa alarma.
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

    def iniciar(self, carpeta: Path) -> None:
        """Lanza FFmpeg y el hilo que vigila su salida. No bloquea."""
        destino = carpeta / f"{self.camara.id}.mkv"
        # Estado nuevo en cada asalto: arrastrar el anterior mostraria en la
        # interfaz frames o errores del asalto ya terminado.
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
                self.estado.frames = int(m.group(1))
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

    def detener(self) -> None:
        """Cierra FFmpeg dejandole finalizar el fichero correctamente.

        Enviar 'q' por stdin es lo que hace que FFmpeg escriba el indice y la
        duracion del MKV antes de salir. Matarlo con terminate() produce un
        fichero reproducible pero sin duracion, que en un reproductor sale sin
        barra de tiempo y complica revisar el asalto.

        Cascada de tres intentos, de mas suave a mas brusco:
            1. 'q' + esperar 8 s  -> cierre limpio, con duracion
            2. terminate() + 5 s  -> fichero valido, sin duracion
            3. kill()             -> ultimo recurso
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
            self._proc.wait(timeout=8)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            # OSError/ValueError: el pipe ya estaba roto o cerrado, lo que pasa
            # si FFmpeg habia muerto justo antes de escribir la 'q'.
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
        self.ruta_cfg = ruta_cfg
        self.cfg = json.loads(ruta_cfg.read_text(encoding="utf-8"))
        self.camaras = [Camara(**c) for c in self.cfg["camaras"]]
        self.raiz = Path(self.cfg["carpeta_grabaciones"])
        self.raiz.mkdir(parents=True, exist_ok=True)

        self.grabadores: list[GrabadorCamara] = []
        self.asalto_actual: dict | None = None

    # Dias de la semana en mayusculas, indexados por datetime.weekday().
    _DIAS = ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO")

    # Carpetas que cuentan como jornada (MIERCOLES_22) y como asalto (007_...).
    # Se exigen exactamente 3 digitos para el asalto: asi una carpeta creada a
    # mano no puede alterar el contador.
    _RE_JORNADA = re.compile(rf"^(?:{'|'.join(_DIAS)})_\d{{2}}$")
    _RE_ASALTO = re.compile(r"^(\d{3})(?:_|$)")

    @classmethod
    def carpeta_dia(cls, momento: datetime | None = None) -> str:
        """Nombre de la carpeta de la jornada: MIERCOLES_22."""
        momento = momento or datetime.now()
        return f"{cls._DIAS[momento.weekday()]}_{momento.day:02d}"

    def siguiente_numero(self) -> int:
        """Numero que se asignara al proximo asalto.

        El contador vive en config.json ('ultimo_asalto'), de modo que no
        depende de como se llamen las carpetas del disco: renombrarlas o
        moverlas ya no altera la numeracion.

        Aun asi se contrasta con lo que hay grabado y se toma el mayor de los
        dos. Es una red de seguridad: si config.json se pierde o se restaura una
        copia antigua, un contador atrasado sobrescribiria asaltos ya grabados.
        """
        guardado = int(self.cfg.get("ultimo_asalto", 0))
        return max(guardado, self._maximo_en_disco()) + 1

    def _maximo_en_disco(self) -> int:
        """Mayor numero de asalto presente en las carpetas ya grabadas.

        Recorre las jornadas (MIERCOLES_22/) buscando carpetas NNN o NNN_...
        Solo se usa como respaldo de 'ultimo_asalto'.

        No basta con que el nombre parezca un asalto: se exige ademas que la
        carpeta contenga metadata.json, que solo escribe esta aplicacion al
        terminar una grabacion. Sin esa comprobacion, una carpeta creada a mano
        como "500_revisar" dispararia el contador a 501 y dejaria un hueco
        enorme en la numeracion.
        """
        if not self.raiz.exists():
            return 0

        maximo = 0
        for jornada in self.raiz.iterdir():
            if not jornada.is_dir() or not self._RE_JORNADA.match(jornada.name):
                continue
            for asalto in jornada.iterdir():
                if not asalto.is_dir() or not (asalto / "metadata.json").exists():
                    continue
                if m := self._RE_ASALTO.match(asalto.name):
                    maximo = max(maximo, int(m.group(1)))
        return maximo

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
            # interrumpe: para el contador, _maximo_en_disco() cubre el hueco.
            pass

    def _guardar_contador(self, numero: int) -> None:
        """Anota en config.json el ultimo numero de asalto usado."""
        self._actualizar_config({"ultimo_asalto": numero})

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
                cam.audio = micro.id if micro else None
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

        El operador escribe libremente ("Garcia vs Lopez") y eso acaba siendo
        un nombre de carpeta que ademas viaja a OneDrive.

        Con flags=UNICODE, \\w conserva letras acentuadas y enes: se quitan los
        signos problematicos (/ \\ : * ? " < > |) pero no se destroza el nombre.
        Los separadores pasan a '_' para encajar con ID_NOMBRE1_NOMBRE2, y se
        corta a 60 caracteres para no acercarse al limite de ruta de Windows.
        """
        limpio = re.sub(r"[^\w\s-]", "", texto, flags=re.UNICODE).strip()
        return re.sub(r"[\s-]+", "_", limpio)[:60].strip("_")

    @property
    def grabando(self) -> bool:
        return self.asalto_actual is not None

    def iniciar_asalto(self, etiqueta: str = "") -> dict:
        """Crea la carpeta del asalto y arranca las tres camaras.

        Vuelve enseguida: las camaras siguen grabando en segundo plano y su
        estado se consulta a traves de self.grabadores[i].estado.
        """
        if self.grabando:
            raise RuntimeError("Ya hay un asalto en curso")

        inicio = datetime.now()
        numero = self.siguiente_numero()

        # ID_NOMBRE1_NOMBRE2 (solo el ID si no se indicaron tiradores),
        # dentro de la carpeta de la jornada: MIERCOLES_22/007_Garcia_Lopez
        sufijo = self._limpiar(etiqueta)
        nombre = f"{numero:03d}" + (f"_{sufijo}" if sufijo else "")
        jornada = self.carpeta_dia(inicio)
        carpeta = self.raiz / jornada / nombre
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
            "etiqueta": etiqueta,
            "carpeta": carpeta,
            "jornada": jornada,
            "inicio": inicio,
        }
        return self.asalto_actual

    def detener_asalto(self) -> dict:
        """Para las tres camaras y deja escrito metadata.json.

        Devuelve la metadata para que la interfaz avise si alguna camara fallo.
        """
        if not self.asalto_actual:
            raise RuntimeError("No hay ningun asalto en curso")

        # Secuencial y bloqueante: cada detener() espera a su FFmpeg. Son unos
        # pocos segundos entre asaltos, y a cambio se garantiza que los tres
        # ficheros estan cerrados y completos antes de escribir la metadata.
        for g in self.grabadores:
            g.detener()

        info = self.asalto_actual
        fin = datetime.now()
        duracion = (fin - info["inicio"]).total_seconds()

        # metadata.json acompana a los videos hasta OneDrive: es el registro de
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
        # no tiene sentido subir a OneDrive). La usa la app para el mosaico.
        resultado = dict(metadata, carpeta=str(info["carpeta"]))

        # Sesion queda libre para el siguiente asalto.
        self.asalto_actual = None
        self.grabadores = []
        return resultado
