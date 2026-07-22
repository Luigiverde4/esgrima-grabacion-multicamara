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

import json
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import dispositivos
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

        self._construir()
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

        # Una fila por camara: (punto de color, etiqueta de detalle).
        # El orden coincide con self.sesion.camaras y con self.sesion.grabadores,
        # y _refrescar() los empareja con zip().
        self.filas = []
        for cam in self.sesion.camaras:
            f = ttk.Frame(mc)
            f.pack(fill="x", pady=3)
            punto = tk.Label(f, text="●", font=("Segoe UI", 15), fg=GRIS)
            punto.pack(side="left")
            ttk.Label(f, text=f"{cam.id} - {cam.nombre}",
                      font=("Segoe UI", 10), width=30, anchor="w").pack(side="left", padx=6)
            info = ttk.Label(f, text="en espera", foreground=GRIS, font=("Segoe UI", 9))
            info.pack(side="left", fill="x", expand=True)
            self.filas.append((punto, info))

        ttk.Button(mc, text="Detectar capturadoras",
                   command=self._detectar).pack(anchor="w", pady=(8, 0))

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

    # ------------------------------------------------------------ dispositivos

    def _detectar(self) -> None:
        """Busca capturadoras y las escribe en config.json.

        Asigna por orden de aparicion, que es el del sistema y no tiene por que
        coincidir con la posicion en la pista. Por eso se avisa de revisarlo:
        confundir el POV frontal con un lateral estropea todo el material.
        """
        nombres = dispositivos.listar_camaras()
        if not nombres:
            messagebox.showwarning("Sin dispositivos", "No se ha detectado ninguna camara.")
            return

        # Iriun, OBS Virtual y similares aparecen como camaras pero no lo son.
        # Sin este aviso, se asignarian creyendo tener las capturadoras puestas.
        if dispositivos.son_virtuales(nombres):
            messagebox.showinfo(
                "Solo camaras virtuales",
                "Lo detectado son webcams virtuales, no capturadoras:\n\n"
                + "\n".join(f"- {n}" for n in nombres)
                + "\n\nConecta las capturadoras HDMI y vuelve a detectar."
            )
            return

        self._escribir(f"Detectadas {len(nombres)} camaras:")
        for n in nombres:
            self._escribir(f"    - {n}")

        # Se relee el fichero en vez de usar self.sesion.cfg para no perder
        # ajustes que se hayan editado a mano mientras la aplicacion corria.
        cfg = json.loads(CFG.read_text(encoding="utf-8"))
        asignadas = 0
        for i, cam in enumerate(cfg["camaras"]):
            if i < len(nombres):   # con menos camaras que ranuras, el resto sigue en None
                cam["dispositivo"] = nombres[i]
                asignadas += 1
        cfg["modo_prueba"] = asignadas == 0
        CFG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

        # Sesion nueva para que tome los dispositivos recien asignados.
        self.sesion = Sesion(CFG)
        self._escribir(f"Asignadas {asignadas} camaras. Revisa el orden en config.json.")
        messagebox.showinfo(
            "Camaras asignadas",
            f"Se han asignado {asignadas} camaras.\n\n"
            "Comprueba que el orden coincide con la posicion real en la pista; "
            "si no, cambialo en config.json."
        )

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
        # una perdida irrecuperable.
        prueba = not any(c.configurada for c in self.sesion.camaras)
        self.lbl_modo.configure(
            text="MODO PRUEBA (testsrc)" if prueba else "Capturadoras conectadas",
            foreground=AMBAR if prueba else VERDE,
        )

        if self.sesion.grabando and self.sesion.asalto_actual:
            info = self.sesion.asalto_actual
            # zip empareja fila i con grabador i: ambas listas siguen el orden
            # de config.json.
            for (punto, lbl), g in zip(self.filas, self.sesion.grabadores):
                e = g.estado
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
                    lbl.configure(text=f"grabando - {e.frames} frames - {mb:.0f} MB",
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
            # En reposo: todo gris y se anuncia el numero del proximo asalto.
            for punto, lbl in self.filas:
                punto.configure(fg=GRIS)
                lbl.configure(text="en espera", foreground=GRIS)
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
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
