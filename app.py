"""ESGRIMA_26 - Control de grabacion multicamara.

Interfaz para grabar asaltos con tres camaras y subirlos a OneDrive.

COMO SE ORGANIZA
    _construir()   monta los widgets una sola vez, al arrancar.
    _refrescar()   se repite cada 500 ms y actualiza los indicadores.
    El resto son manejadores de los botones.

DOS FORMAS DE RECIBIR INFORMACION, Y POR QUE
    Grabacion -> por sondeo. _refrescar() lee los EstadoCamara del motor. Los
    hilos de FFmpeg no llaman nunca a la interfaz: Tkinter no admite que se
    toquen sus widgets desde otro hilo, y sondear cada 500 ms evita el problema
    de raiz. Ademas mantiene el motor sin saber que existe una GUI.

    Subida -> por callback. rclone corre en un hilo y avisa del progreso, asi
    que esos avisos se encolan con self.after(0, ...) para que se ejecuten en
    el hilo de Tkinter. Tocar widgets directamente desde ahi cuelga la ventana
    de forma intermitente y dificil de reproducir.
"""

import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import dispositivos
import mosaico
import subida
from grabador import Sesion

RAIZ = Path(__file__).parent
CFG = RAIZ / "config.json"

# Colores de estado, pensados para leerse de un vistazo desde lejos.
VERDE, ROJO, AMBAR, GRIS = "#1a7f37", "#c9252d", "#bf8700", "#57606a"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ESGRIMA_26 - Grabacion de asaltos")
        self.geometry("760x600")
        self.minsize(680, 560)

        self.sesion = Sesion(CFG)   # motor: lee config.json y prepara camaras
        self.subiendo = False       # bloquea grabar y subir a la vez
        self._firma_lista = None    # evita repintar la lista sin cambios
        self._previews: dict[str, subprocess.Popen] = {}  # ffplay por camara
        self._video_disp: list = []   # ultimo listado de Dispositivo (video)
        self._audio_disp: list = []   # ultimo listado de Dispositivo (audio)
        self._map_video: dict = {}    # etiqueta visible -> Dispositivo
        self._map_audio: dict = {}

        self._construir()
        # Rellena los desplegables una vez montada la interfaz (necesita el log).
        self._refrescar_dispositivos()
        self._refrescar()           # arranca el ciclo de refresco permanente
        # Interceptar el cierre para no perder un asalto a medio grabar.
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)

    # ---------------------------------------------------------------- interfaz

    def _construir(self) -> None:
        """Monta la ventana. Los widgets que luego se actualizan se guardan
        en self (lbl_modo, btn, filas, barra...); el resto son decorativos.
        """
        cont = ttk.Frame(self, padding=14)
        cont.pack(fill="both", expand=True)

        cab = ttk.Frame(cont)
        cab.pack(fill="x", pady=(0, 10))
        ttk.Label(cab, text="ESGRIMA_26", font=("Segoe UI", 17, "bold")).pack(side="left")
        self.lbl_modo = ttk.Label(cab, font=("Segoe UI", 9))
        self.lbl_modo.pack(side="right")

        # --- Asalto ---
        marco = ttk.LabelFrame(cont, text="Asalto", padding=12)
        marco.pack(fill="x", pady=(0, 10))

        fila = ttk.Frame(marco)
        fila.pack(fill="x")
        ttk.Label(fila, text="Tiradores:").pack(side="left")
        self.entrada = ttk.Entry(fila, font=("Segoe UI", 11))
        self.entrada.pack(side="left", fill="x", expand=True, padx=8)
        # Enter inicia o para el asalto: mas rapido que buscar el boton con el
        # raton mientras se sigue lo que pasa en la pista.
        self.entrada.bind("<Return>", lambda _: self._alternar())
        ttk.Label(fila, text="(opcional)", foreground=GRIS).pack(side="left")

        # tk.Button en vez de ttk.Button: ttk no deja fijar el color de fondo
        # en Windows, y aqui el verde/rojo es la senal principal de estado.
        self.btn = tk.Button(
            marco, text="INICIAR ASALTO", font=("Segoe UI", 15, "bold"),
            bg=VERDE, fg="white", height=2, relief="flat",
            activebackground=VERDE, activeforeground="white",
            cursor="hand2", command=self._alternar,
        )
        self.btn.pack(fill="x", pady=(12, 6))

        self.lbl_asalto = ttk.Label(marco, text="", font=("Segoe UI", 10))
        self.lbl_asalto.pack()

        # --- Camaras ---
        mc = ttk.LabelFrame(cont, text="Camaras", padding=12)
        mc.pack(fill="x", pady=(0, 10))

        # Texto que representa "sin dispositivo" en los desplegables. Se elige
        # una cadena que ningun dispositivo real puede tener como nombre.
        self.MODO_PRUEBA = "-- Modo prueba (patron) --"
        self.SIN_AUDIO = "-- Sin audio --"

        # Etiquetas visibles del combo de formato -> valor interno.
        self.FORMATOS = {"MJPEG": "mjpeg", "YUYV": "yuyv422", "Auto": "auto"}

        # Una fila (dos lineas) por camara. Cada fila guarda: punto de color,
        # combo de video, combo de audio, combo de formato, etiqueta de estado y
        # boton Ver. El orden coincide con self.sesion.camaras y con
        # self.sesion.grabadores; _refrescar() los empareja con zip().
        self.filas = []
        for cam in self.sesion.camaras:
            marco_cam = ttk.Frame(mc)
            marco_cam.pack(fill="x", pady=(4, 8))

            # Linea superior: punto, nombre, combo de video, boton Ver.
            arriba = ttk.Frame(marco_cam)
            arriba.pack(fill="x")

            punto = tk.Label(arriba, text="●", font=("Segoe UI", 15), fg=GRIS)
            punto.pack(side="left")
            ttk.Label(arriba, text=f"{cam.id} · {cam.nombre}",
                      font=("Segoe UI", 10), width=22, anchor="w").pack(side="left", padx=(6, 8))

            combo = ttk.Combobox(arriba, state="readonly", font=("Segoe UI", 9))
            combo.pack(side="left", fill="x", expand=True)
            # El id de camara se captura en la closure, no en un atributo del
            # widget: asi el callback sabe a que camara aplica sin depender de
            # atributos dinamicos.
            combo.bind("<<ComboboxSelected>>",
                       lambda _e, cid=cam.id, cb=combo: self._elegir_dispositivo(cid, cb))

            ver = ttk.Button(arriba, text="Ver", width=5,
                             command=lambda cid=cam.id: self._previsualizar(cid))
            ver.pack(side="left", padx=(6, 0))

            info = ttk.Label(arriba, text="", foreground=GRIS, font=("Segoe UI", 9))
            info.pack(side="left", padx=(8, 0))

            # Linea inferior: combo de audio (micro), alineado bajo el de video.
            abajo = ttk.Frame(marco_cam)
            abajo.pack(fill="x", pady=(3, 0))
            ttk.Label(abajo, text="micro", foreground=GRIS, font=("Segoe UI", 8),
                      width=22, anchor="e").pack(side="left", padx=(0, 8))
            combo_audio = ttk.Combobox(abajo, state="readonly", font=("Segoe UI", 9))
            combo_audio.pack(side="left", fill="x", expand=True)
            combo_audio.bind("<<ComboboxSelected>>",
                             lambda _e, cid=cam.id, cb=combo_audio: self._elegir_audio(cid, cb))

            # Combo de formato de entrada (MJPEG por defecto, ver _entrada()).
            combo_fmt = ttk.Combobox(abajo, state="readonly", width=7,
                                     font=("Segoe UI", 9), values=list(self.FORMATOS))
            combo_fmt.pack(side="left", padx=(6, 0))
            combo_fmt.bind("<<ComboboxSelected>>",
                           lambda _e, cid=cam.id, cb=combo_fmt: self._elegir_formato(cid, cb))

            self.filas.append((punto, combo, combo_audio, combo_fmt, info, ver))

        pie = ttk.Frame(mc)
        pie.pack(fill="x", pady=(8, 0))
        ttk.Button(pie, text="↻ Refrescar lista",
                   command=self._refrescar_dispositivos).pack(side="left")
        self.lbl_disp = ttk.Label(pie, text="", foreground=GRIS, font=("Segoe UI", 9))
        self.lbl_disp.pack(side="left", padx=10)

        # --- Subida ---
        ms = ttk.LabelFrame(cont, text="Subida a OneDrive", padding=12)
        ms.pack(fill="x", pady=(0, 10))

        f = ttk.Frame(ms)
        f.pack(fill="x")
        self.btn_subir = ttk.Button(f, text="Subir todo a OneDrive", command=self._subir)
        self.btn_subir.pack(side="left")
        ttk.Button(f, text="Abrir carpeta local",
                   command=self._abrir_carpeta).pack(side="left", padx=6)
        ttk.Label(f, text=self.sesion.cfg["rclone_destino"],
                  foreground=GRIS, font=("Segoe UI", 9)).pack(side="right")

        # Lista de asaltos pendientes de subir. Se rellena en _refrescar() y
        # durante la subida marca cual se esta transfiriendo en cada momento.
        flista = ttk.Frame(ms)
        flista.pack(fill="x", pady=(8, 0))
        self.lista = tk.Listbox(
            flista, height=5, font=("Consolas", 9), relief="flat",
            activestyle="none", highlightthickness=0,
            selectbackground="#dbeafe", selectforeground="#111",
        )
        barra_lat = ttk.Scrollbar(flista, orient="vertical", command=self.lista.yview)
        self.lista.configure(yscrollcommand=barra_lat.set)
        self.lista.pack(side="left", fill="both", expand=True)
        barra_lat.pack(side="right", fill="y")

        self.barra = ttk.Progressbar(ms, mode="determinate")
        self.barra.pack(fill="x", pady=(8, 4))
        self.lbl_subida = ttk.Label(ms, text="", foreground=GRIS, font=("Segoe UI", 9))
        self.lbl_subida.pack(anchor="w")

        # --- Registro ---
        mr = ttk.LabelFrame(cont, text="Registro", padding=8)
        mr.pack(fill="both", expand=True)
        self.log = tk.Text(mr, height=6, font=("Consolas", 9),
                           state="disabled", wrap="word", relief="flat")
        self.log.pack(fill="both", expand=True)

    def _escribir(self, texto: str) -> None:
        """Anade una linea al registro.

        El widget esta en state='disabled' para que no se pueda editar a mano;
        hay que habilitarlo, escribir y volver a deshabilitarlo.
        """
        self.log.configure(state="normal")
        self.log.insert("end", texto + "\n")
        self.log.see("end")          # sigue siempre la ultima linea
        self.log.configure(state="disabled")

    # ------------------------------------------------------------- grabacion

    def _alternar(self) -> None:
        """Un unico boton para iniciar y parar: imposible equivocarse de sitio."""
        if self.sesion.grabando:
            self._detener()
        else:
            self._iniciar()

    def _iniciar(self) -> None:
        # Cerrar las previews primero: DirectShow no deja que ffplay y FFmpeg
        # abran la misma capturadora a la vez, y la grabacion fallaria.
        self._cerrar_previews()
        try:
            info = self.sesion.iniciar_asalto(self.entrada.get().strip())
        except Exception as e:
            # Aqui se captura todo a proposito: un fallo al arrancar (permisos,
            # disco lleno, config invalida) debe avisar al operador, nunca
            # cerrar la aplicacion en mitad de la competicion.
            messagebox.showerror("Error", str(e))
            return

        self._escribir(f"[{info['inicio']:%H:%M:%S}] Inicio asalto {info['numero']:03d}"
                       + (f" - {info['etiqueta']}" if info["etiqueta"] else ""))
        self.btn.configure(text="DETENER ASALTO", bg=ROJO, activebackground=ROJO)
        self.entrada.configure(state="disabled")   # el nombre ya no puede cambiar
        self.btn_subir.configure(state="disabled")  # no subir mientras se graba

    def _detener(self) -> None:
        # Bloquea unos segundos mientras se cierran los tres FFmpeg. La ventana
        # se queda quieta ese rato; es preferible a devolver el control con los
        # ficheros aun sin cerrar.
        meta = self.sesion.detener_asalto()

        # Fichero de 0 bytes = camara que no grabo nada, aunque no diera error.
        fallos = [c for c in meta["camaras"] if c["error"] or c["tamano_bytes"] == 0]

        self._escribir(
            f"[{meta['fin'][11:]}] Fin asalto {meta['asalto']:03d} "
            f"({meta['duracion_s']:.0f}s) -> {len(meta['camaras']) - len(fallos)}/"
            f"{len(meta['camaras'])} camaras OK"
        )
        for c in fallos:
            self._escribir(f"    ! {c['id']}: {c['error'] or 'fichero vacio'}")

        # Mosaico automatico: solo si las tres camaras grabaron bien. Se hace en
        # segundo plano para no congelar la app entre asaltos.
        self._generar_mosaico(meta, fallos)

        self.btn.configure(text="INICIAR ASALTO", bg=VERDE, activebackground=VERDE)
        self.entrada.configure(state="normal")
        self.entrada.delete(0, "end")
        if not self.subiendo:
            self.btn_subir.configure(state="normal")

        # Aviso modal a proposito: si una camara ha fallado, el operador debe
        # enterarse ahora y poder revisarla antes del siguiente asalto, no al
        # descubrir el hueco esa noche.
        if fallos:
            messagebox.showwarning(
                "Asalto grabado con incidencias",
                f"{len(fallos)} de {len(meta['camaras'])} camaras han fallado.\n\n"
                + "\n".join(f"- {c['id']}: {c['error'] or 'fichero vacio'}" for c in fallos)
            )

    def _generar_mosaico(self, meta: dict, fallos: list) -> None:
        """Lanza la generacion del mosaico en segundo plano, si procede.

        Solo si las tres camaras grabaron bien: un mosaico al que le falta un
        POV no aporta. El orden de camaras en config es [izq, frontal, der], asi
        que el frontal es el del medio.
        """
        if fallos or len(meta["camaras"]) != 3:
            return
        carpeta = Path(meta["carpeta"])
        # Ficheros por posicion: 0=lateral izq, 1=frontal, 2=lateral der.
        ficheros = [c["fichero"] for c in meta["camaras"]]
        if not all(ficheros):
            return
        izq, frontal, der = ficheros

        self._escribir(f"Generando mosaico del asalto {meta['asalto']:03d}...")
        hilo = mosaico.generar(
            carpeta, frontal=frontal, izquierda=izq, derecha=der,
            al_terminar=self._fin_mosaico,
        )
        if hilo is None:
            self._escribir("    ! mosaico omitido: falta algun video")

    def _fin_mosaico(self, ok: bool, mensaje: str) -> None:
        # Llamado desde el hilo del mosaico: al hilo de Tkinter con after.
        self.after(0, lambda: self._escribir(("" if ok else "    ! ") + mensaje))

    # ------------------------------------------------------------ dispositivos

    def _refrescar_dispositivos(self) -> None:
        """Rellena cada desplegable con los dispositivos conectados ahora.

        La opcion de modo prueba va siempre la primera. Se conserva la eleccion
        guardada de cada camara aunque su dispositivo no este conectado en este
        momento: asi no se pierde la configuracion si se refresca con un cable
        suelto (se marca como no disponible, pero no se borra).
        """
        if self.sesion.grabando:
            return  # no cambiar dispositivos con una grabacion en curso

        # Video y audio en una sola llamada a FFmpeg, y en memoria para que
        # _elegir_dispositivo pueda autoemparejar sin volver a enumerar.
        self._video_disp, self._audio_disp = dispositivos.listar_video_y_audio()

        # Mapas etiqueta_visible -> Dispositivo. Los combos muestran etiquetas
        # legibles (desambiguadas si hay nombres repetidos) pero por dentro se
        # trabaja con el id de hardware. Se reconstruyen en cada refresco.
        self._map_video = self._construir_mapa(self._video_disp)
        self._map_audio = self._construir_mapa(self._audio_disp)

        self.lbl_disp.configure(
            text=(f"{len(self._video_disp)} video · {len(self._audio_disp)} audio"
                  if self._video_disp else "ningun dispositivo detectado")
        )
        self._escribir(f"Detectados: {len(self._video_disp)} video, "
                       f"{len(self._audio_disp)} audio")

        # Etiqueta visible del formato guardado de cada camara (mjpeg -> "MJPEG").
        fmt_a_etiqueta = {v: k for k, v in self.FORMATOS.items()}

        for (_punto, combo, combo_audio, combo_fmt, _info, _ver), cam in zip(
                self.filas, self.sesion.camaras):
            self._rellenar_combo_video(combo, cam)
            self._rellenar_combo_audio(combo_audio, cam)
            combo_fmt.set(fmt_a_etiqueta.get(cam.formato, "MJPEG"))
            # El formato solo aplica con capturadora real.
            combo_fmt.configure(state="readonly" if cam.configurada else "disabled")

    @staticmethod
    def _construir_mapa(disps: list) -> dict:
        """Mapa etiqueta -> Dispositivo, desambiguando nombres repetidos.

        Si dos dispositivos comparten nombre (dos capturadoras identicas), se les
        anade '(1)', '(2)'... por orden de aparicion, solo en la etiqueta visible.
        """
        cuenta: dict[str, int] = {}
        for d in disps:
            cuenta[d.nombre] = cuenta.get(d.nombre, 0) + 1
        vistos: dict[str, int] = {}
        mapa: dict = {}
        for d in disps:
            if cuenta[d.nombre] > 1:
                vistos[d.nombre] = vistos.get(d.nombre, 0) + 1
                mapa[d.etiqueta(vistos[d.nombre])] = d
            else:
                mapa[d.etiqueta()] = d
        return mapa

    def _etiqueta_guardada(self, ident: str | None, nombre: str | None,
                           mapa: dict, vacio: str) -> str:
        """Etiqueta a mostrar para un id guardado.

        Si el id sigue conectado, se usa su etiqueta actual del mapa. Si no, se
        muestra el nombre guardado marcado '(no disponible)' para no perder la
        referencia visible; el id permanece guardado igual.
        """
        if not ident:
            return vacio
        for etiqueta, disp in mapa.items():
            if disp.id == ident:
                return etiqueta
        return f"{nombre or ident}  (no disponible)"

    def _rellenar_combo_video(self, combo: ttk.Combobox, cam) -> None:
        """Opciones y valor del desplegable de video de una camara."""
        etiqueta = self._etiqueta_guardada(cam.dispositivo, cam.dispositivo_nombre,
                                           self._map_video, self.MODO_PRUEBA)
        opciones = [self.MODO_PRUEBA] + list(self._map_video)
        if etiqueta not in opciones and etiqueta != self.MODO_PRUEBA:
            opciones.append(etiqueta)  # el guardado ya no esta conectado
        combo.configure(values=opciones)
        combo.set(etiqueta)

    def _rellenar_combo_audio(self, combo: ttk.Combobox, cam) -> None:
        """Opciones y valor del desplegable de audio (micro) de una camara.

        En modo prueba el audio no aplica: el combo queda deshabilitado.
        """
        if not cam.configurada:
            combo.configure(values=[self.SIN_AUDIO], state="disabled")
            combo.set(self.SIN_AUDIO)
            return
        etiqueta = self._etiqueta_guardada(cam.audio, cam.audio,
                                           self._map_audio, self.SIN_AUDIO)
        opciones = [self.SIN_AUDIO] + list(self._map_audio)
        if etiqueta not in opciones and etiqueta != self.SIN_AUDIO:
            opciones.append(etiqueta)
        combo.configure(values=opciones, state="readonly")
        combo.set(etiqueta)

    def _elegir_dispositivo(self, cam_id: str, combo: ttk.Combobox) -> None:
        """Guarda la eleccion de video; el micro se autoempareja."""
        if self.sesion.grabando:
            return
        etiqueta = combo.get()
        # Traduce la etiqueta visible al Dispositivo (o None en modo prueba).
        dispositivo = self._map_video.get(etiqueta) if etiqueta != self.MODO_PRUEBA else None

        self.sesion.asignar_video(cam_id, dispositivo, self._audio_disp)
        cam = next(c for c in self.sesion.camaras if c.id == cam_id)
        if dispositivo:
            msg = f"{cam_id} -> {dispositivo.nombre}"
            if cam.audio:
                msg += f"  ·  micro emparejado"
            elif self._audio_disp:
                # Habia micros pero ninguno emparejo con seguridad: avisar.
                msg += "  ·  micro NO emparejado, eligelo a mano"
            self._escribir(msg)
        else:
            self._escribir(f"{cam_id} -> modo prueba")
        # Refleja el micro autoemparejado (o su ausencia) en los combos.
        self._refrescar_dispositivos()

    def _elegir_audio(self, cam_id: str, combo: ttk.Combobox) -> None:
        """Guarda el micro elegido a mano para una camara."""
        if self.sesion.grabando:
            return
        etiqueta = combo.get()
        disp = self._map_audio.get(etiqueta) if etiqueta != self.SIN_AUDIO else None
        self.sesion.guardar_audio(cam_id, disp.id if disp else None)
        self._escribir(f"{cam_id} · micro -> {disp.nombre if disp else 'sin audio'}")

    def _elegir_formato(self, cam_id: str, combo: ttk.Combobox) -> None:
        """Guarda el formato de entrada elegido (MJPEG/YUYV/Auto)."""
        if self.sesion.grabando:
            return
        formato = self.FORMATOS.get(combo.get(), "mjpeg")
        self.sesion.guardar_formato(cam_id, formato)
        self._escribir(f"{cam_id} · formato -> {formato}")

    def _previsualizar(self, cam_id: str) -> None:
        """Abre (o cierra) una ventana de ffplay con la imagen en vivo.

        Si ya hay una preview de esa camara abierta, el boton la cierra: asi el
        mismo boton sirve para abrir y quitar. No funciona en modo prueba (no
        hay dispositivo real que mostrar) ni durante la grabacion.
        """
        # Si la preview anterior sigue viva, este segundo clic la cierra.
        proc = self._previews.get(cam_id)
        if proc and proc.poll() is None:
            proc.terminate()
            self._previews.pop(cam_id, None)
            self._escribir(f"{cam_id} -> previsualizacion cerrada")
            return

        cam = next((c for c in self.sesion.camaras if c.id == cam_id), None)
        if not cam or not cam.dispositivo:
            messagebox.showinfo(
                "Sin dispositivo",
                "Esta camara esta en modo prueba.\n\n"
                "Elige una capturadora en el desplegable para previsualizarla."
            )
            return

        v = self.sesion.cfg["video"]
        proc = dispositivos.previsualizar(cam.dispositivo, v["resolucion"], v["fps"])
        if proc is None:
            messagebox.showerror("Error", "No se encontro ffplay en el PATH.")
            return

        self._previews[cam_id] = proc
        self._escribir(f"{cam_id} -> previsualizando {cam.dispositivo_nombre or cam.dispositivo}")

    def _cerrar_previews(self) -> None:
        """Cierra todas las ventanas de previsualizacion abiertas."""
        for proc in self._previews.values():
            if proc.poll() is None:
                proc.terminate()
        self._previews.clear()

    # ---------------------------------------------------------------- subida

    def _asaltos_en_disco(self) -> list[Path]:
        """Asaltos grabados, ordenados. Cuelgan de la jornada: MIERCOLES_22/007_..."""
        return sorted(d for d in self.sesion.raiz.glob("*/*") if d.is_dir())

    def _pintar_lista(self, actual: str = "") -> None:
        """Rellena el recuadro con los asaltos y su tamano.

        'actual' es el asalto que rclone esta subiendo ahora ('JORNADA/ASALTO');
        se marca con una flecha y se hace visible desplazando la lista.
        """
        asaltos = self._asaltos_en_disco()

        # Reconstruir la lista entera en cada refresco haria parpadear la
        # seleccion, asi que solo se rehace cuando su contenido cambia.
        firma = (tuple(str(d) for d in asaltos), actual)
        if firma == self._firma_lista:
            return
        self._firma_lista = firma

        self.lista.delete(0, "end")
        if not asaltos:
            self.lista.insert("end", "  (sin asaltos grabados)")
            return

        for d in asaltos:
            mb = sum(f.stat().st_size for f in d.glob("*.mkv") if f.is_file()) / 1e6
            etiqueta = f"{d.parent.name}/{d.name}"
            incompleto = "" if (d / "metadata.json").exists() else "  [sin metadata]"
            marca = "> " if etiqueta == actual else "  "
            self.lista.insert("end", f"{marca}{etiqueta}   {mb:.0f} MB{incompleto}")
            if etiqueta == actual:
                self.lista.selection_clear(0, "end")
                self.lista.selection_set("end")
                self.lista.see("end")

    def _subir(self) -> None:
        """Sube todas las grabaciones a OneDrive. Pensado para el final del dia."""
        # Doble red de seguridad: los botones ya se deshabilitan, pero el atajo
        # de teclado o un doble clic podrian colarse igualmente.
        if self.subiendo or self.sesion.grabando:
            return
        carpetas = self._asaltos_en_disco()
        if not carpetas:
            messagebox.showinfo("Nada que subir", "No hay asaltos grabados.")
            return

        # metadata.json solo existe si el asalto se cerro bien. Sin el, la
        # carpeta es un asalto interrumpido o algo creado a mano: se avisa,
        # pero se deja decidir al operador (ese material puede ser valioso).
        incompletas = [d for d in carpetas if not (d / "metadata.json").exists()]

        destino = self.sesion.cfg["rclone_destino"]
        aviso = ""
        if incompletas:
            nombres = ", ".join(d.name for d in incompletas[:4])
            if len(incompletas) > 4:
                nombres += f" y {len(incompletas) - 4} mas"
            aviso = (f"\n\nAtencion: {len(incompletas)} carpeta(s) sin metadata "
                     f"(asalto interrumpido?): {nombres}")

        if not messagebox.askyesno(
            "Confirmar subida",
            f"Se subiran {len(carpetas)} asaltos a:\n{destino}\n\n"
            "No se borrara nada del destino." + aviso + "\n\nContinuar?"
        ):
            return

        self.subiendo = True
        # Se bloquea tambien grabar: si empezara un asalto durante la subida,
        # rclone podria leer un fichero a medio escribir.
        self.btn_subir.configure(state="disabled")
        self.btn.configure(state="disabled")
        self.barra.configure(value=0)
        self._escribir(f"Subiendo {len(carpetas)} asaltos a {destino}...")

        # Arranca en un hilo y vuelve enseguida; el avance llega por callback.
        subida.subir(self.sesion.raiz, destino, self._avance_subida, self._fin_subida)

    # Los dos metodos siguientes los invoca el hilo de rclone, NO la interfaz.
    # De ahi el self.after(0, ...): encola el trabajo en el hilo de Tkinter,
    # que es el unico que puede tocar widgets sin provocar cuelgues.

    def _avance_subida(self, prog: subida.Progreso) -> None:
        def aplicar():
            self.barra.configure(value=prog.pct)
            self.lbl_subida.configure(text=prog.resumen(), foreground=GRIS)
            # Marca en la lista el asalto que se esta subiendo ahora.
            self._pintar_lista(prog.asalto)
        self.after(0, aplicar)

    def _fin_subida(self, ok: bool, mensaje: str) -> None:
        def aplicar():
            self.subiendo = False
            self.btn_subir.configure(state="normal")
            self.btn.configure(state="normal")
            self.barra.configure(value=100 if ok else 0)
            self.lbl_subida.configure(text=mensaje, foreground=VERDE if ok else ROJO)
            # Quita la marca del asalto en curso y deja la lista en reposo.
            self.lista.selection_clear(0, "end")
            self._firma_lista = None
            self._pintar_lista()
            self._escribir(mensaje)
            if ok:
                messagebox.showinfo("Subida completada", "Los asaltos estan en OneDrive.")
            else:
                messagebox.showerror("Error en la subida", mensaje)
        self.after(0, aplicar)

    def _abrir_carpeta(self) -> None:
        self.sesion.raiz.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(self.sesion.raiz.resolve())])

    # -------------------------------------------------------------- refresco

    def _refrescar(self) -> None:
        """Actualiza el estado visual cada 500 ms.

        Es el unico sitio donde se pintan los indicadores de camara. Se
        reprograma solo al final, asi que el ciclo no se detiene nunca mientras
        la ventana este abierta.

        500 ms es un compromiso: lo bastante rapido para que el operador vea el
        fallo casi al instante, y lo bastante lento para no consumir CPU que
        necesitan los tres FFmpeg.
        """
        # Aviso permanente de modo prueba: grabar un asalto real creyendo tener
        # las capturadoras puestas, y acabar con tres patrones de barras, seria
        # una perdida irrecuperable. En el otro caso solo se afirma que hay
        # dispositivo asignado, no que este dando senal: eso lo dicen los
        # semaforos durante la grabacion.
        prueba = not any(c.configurada for c in self.sesion.camaras)
        self.lbl_modo.configure(
            text="MODO PRUEBA (testsrc)" if prueba else "Dispositivos asignados",
            foreground=AMBAR if prueba else VERDE,
        )

        if self.sesion.grabando and self.sesion.asalto_actual:
            info = self.sesion.asalto_actual
            # zip empareja fila i con grabador i: ambas listas siguen el orden
            # de config.json.
            for (punto, combo, combo_audio, combo_fmt, lbl, ver), g in zip(
                    self.filas, self.sesion.grabadores):
                combo.configure(state="disabled")        # no cambiar fuente al vuelo
                combo_audio.configure(state="disabled")
                combo_fmt.configure(state="disabled")
                ver.configure(state="disabled")          # no previsualizar mientras graba
                e = g.estado
                sufijo_audio = " ♪" if g.camara.con_audio else ""
                # Orden de prioridad: primero lo mas grave.
                if e.error:
                    punto.configure(fg=ROJO)
                    lbl.configure(text=e.error, foreground=ROJO)
                elif e.bloqueada:
                    # Sigue grabando, pero el contador de frames no avanza:
                    # tipicamente un HDMI suelto. El fichero crece con imagen fija.
                    punto.configure(fg=AMBAR)
                    lbl.configure(text="SIN SENAL - imagen congelada", foreground=AMBAR)
                elif e.grabando:
                    punto.configure(fg=VERDE)
                    mb = (e.fichero.stat().st_size / 1e6
                          if e.fichero and e.fichero.exists() else 0)
                    lbl.configure(text=f"grabando - {e.frames} frames - {mb:.0f} MB{sufijo_audio}",
                                  foreground=VERDE)
                else:
                    # Proceso terminado sin error registrado: no deberia pasar
                    # durante un asalto, asi que se marca en rojo igualmente.
                    punto.configure(fg=ROJO)
                    lbl.configure(text="detenida", foreground=ROJO)
            self.lbl_asalto.configure(
                text=f"Asalto {info['numero']:03d} en curso  -  {info['jornada']}"
            )
        else:
            # En reposo: el punto refleja si la camara tiene fuente asignada
            # (verde apagado) o esta en modo prueba (gris). El combo vuelve a
            # ser seleccionable y la etiqueta de estado se limpia.
            for (punto, combo, combo_audio, combo_fmt, lbl, ver), cam in zip(
                    self.filas, self.sesion.camaras):
                punto.configure(fg=VERDE if cam.configurada else GRIS)
                combo.configure(state="readonly")
                # Audio y formato solo tienen sentido con video real.
                estado_extra = "readonly" if cam.configurada else "disabled"
                combo_audio.configure(state=estado_extra)
                combo_fmt.configure(state=estado_extra)
                ver.configure(state="normal")
                lbl.configure(text="")
            n = self.sesion.siguiente_numero()
            self.lbl_asalto.configure(
                text=f"Listo - siguiente: asalto {n:03d}  -  {self.sesion.carpeta_dia()}"
            )

        # La lista de asaltos solo se toca cuando no hay subida en marcha: si la
        # hay, la mantiene _avance_subida() para marcar el que se transfiere.
        if not self.subiendo:
            self._pintar_lista()

        # Se reencola el proximo refresco: aqui es donde el ciclo se perpetua.
        self.after(500, self._refrescar)

    def _al_cerrar(self) -> None:
        """Impide cerrar por accidente con un asalto o una subida en marcha.

        Si se acepta cerrar durante una grabacion, se detiene bien: los MKV
        quedan cerrados y con su metadata, no a medias.
        """
        if self.sesion.grabando:
            if not messagebox.askyesno("Grabacion en curso",
                                       "Hay un asalto grabando. Detener y salir?"):
                return
            self.sesion.detener_asalto()
        if self.subiendo and not messagebox.askyesno(
                "Subida en curso", "La subida no ha terminado. Salir igualmente?"):
            return
        self._cerrar_previews()  # no dejar ventanas de ffplay huerfanas
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
