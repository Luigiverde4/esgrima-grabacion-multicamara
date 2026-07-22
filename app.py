"""ESGRIMA_26 - Control de grabacion multicamara.

Interfaz para grabar asaltos con tres camaras y subirlos a OneDrive.
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

        self.sesion = Sesion(CFG)
        self.subiendo = False

        self._construir()
        self._refrescar()
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)

    # ---------------------------------------------------------------- interfaz

    def _construir(self) -> None:
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
        self.entrada.bind("<Return>", lambda _: self._alternar())
        ttk.Label(fila, text="(opcional)", foreground=GRIS).pack(side="left")

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
        self.log.configure(state="normal")
        self.log.insert("end", texto + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------- grabacion

    def _alternar(self) -> None:
        if self.sesion.grabando:
            self._detener()
        else:
            self._iniciar()

    def _iniciar(self) -> None:
        try:
            info = self.sesion.iniciar_asalto(self.entrada.get().strip())
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return

        self._escribir(f"[{info['inicio']:%H:%M:%S}] Inicio asalto {info['numero']:03d}"
                       + (f" - {info['etiqueta']}" if info["etiqueta"] else ""))
        self.btn.configure(text="DETENER ASALTO", bg=ROJO, activebackground=ROJO)
        self.entrada.configure(state="disabled")
        self.btn_subir.configure(state="disabled")

    def _detener(self) -> None:
        meta = self.sesion.detener_asalto()
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

        if fallos:
            messagebox.showwarning(
                "Asalto grabado con incidencias",
                f"{len(fallos)} de {len(meta['camaras'])} camaras han fallado.\n\n"
                + "\n".join(f"- {c['id']}: {c['error'] or 'fichero vacio'}" for c in fallos)
            )

    # ------------------------------------------------------------ dispositivos

    def _detectar(self) -> None:
        nombres = dispositivos.listar_camaras()
        if not nombres:
            messagebox.showwarning("Sin dispositivos", "No se ha detectado ninguna camara.")
            return

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

        cfg = json.loads(CFG.read_text(encoding="utf-8"))
        asignadas = 0
        for i, cam in enumerate(cfg["camaras"]):
            if i < len(nombres):
                cam["dispositivo"] = nombres[i]
                asignadas += 1
        cfg["modo_prueba"] = asignadas == 0
        CFG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

        self.sesion = Sesion(CFG)
        self._escribir(f"Asignadas {asignadas} camaras. Revisa el orden en config.json.")
        messagebox.showinfo(
            "Camaras asignadas",
            f"Se han asignado {asignadas} camaras.\n\n"
            "Comprueba que el orden coincide con la posicion real en la pista; "
            "si no, cambialo en config.json."
        )

    # ---------------------------------------------------------------- subida

    def _subir(self) -> None:
        if self.subiendo or self.sesion.grabando:
            return
        carpetas = [d for d in self.sesion.raiz.glob("asalto_*") if d.is_dir()]
        if not carpetas:
            messagebox.showinfo("Nada que subir", "No hay asaltos grabados.")
            return

        destino = self.sesion.cfg["rclone_destino"]
        if not messagebox.askyesno(
            "Confirmar subida",
            f"Se subiran {len(carpetas)} asaltos a:\n{destino}\n\n"
            "No se borrara nada del destino. Continuar?"
        ):
            return

        self.subiendo = True
        self.btn_subir.configure(state="disabled")
        self.btn.configure(state="disabled")
        self.barra.configure(value=0)
        self._escribir(f"Subiendo {len(carpetas)} asaltos a {destino}...")

        subida.subir(self.sesion.raiz, destino, self._avance_subida, self._fin_subida)

    def _avance_subida(self, linea: str, pct: int) -> None:
        def aplicar():
            if pct >= 0:
                self.barra.configure(value=pct)
            self.lbl_subida.configure(text=linea[:110])
        self.after(0, aplicar)

    def _fin_subida(self, ok: bool, mensaje: str) -> None:
        def aplicar():
            self.subiendo = False
            self.btn_subir.configure(state="normal")
            self.btn.configure(state="normal")
            self.barra.configure(value=100 if ok else 0)
            self.lbl_subida.configure(text=mensaje, foreground=VERDE if ok else ROJO)
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
        """Actualiza el estado visual cada 500 ms."""
        prueba = not any(c.configurada for c in self.sesion.camaras)
        self.lbl_modo.configure(
            text="MODO PRUEBA (testsrc)" if prueba else "Capturadoras conectadas",
            foreground=AMBAR if prueba else VERDE,
        )

        if self.sesion.grabando and self.sesion.asalto_actual:
            info = self.sesion.asalto_actual
            for (punto, lbl), g in zip(self.filas, self.sesion.grabadores):
                e = g.estado
                if e.error:
                    punto.configure(fg=ROJO)
                    lbl.configure(text=e.error, foreground=ROJO)
                elif e.bloqueada:
                    punto.configure(fg=AMBAR)
                    lbl.configure(text="SIN SENAL - imagen congelada", foreground=AMBAR)
                elif e.grabando:
                    punto.configure(fg=VERDE)
                    mb = (e.fichero.stat().st_size / 1e6
                          if e.fichero and e.fichero.exists() else 0)
                    lbl.configure(text=f"grabando - {e.frames} frames - {mb:.0f} MB",
                                  foreground=VERDE)
                else:
                    punto.configure(fg=ROJO)
                    lbl.configure(text="detenida", foreground=ROJO)
            self.lbl_asalto.configure(text=f"Asalto {info['numero']:03d} en curso")
        else:
            for punto, lbl in self.filas:
                punto.configure(fg=GRIS)
                lbl.configure(text="en espera", foreground=GRIS)
            n = self.sesion.siguiente_numero()
            self.lbl_asalto.configure(text=f"Listo - siguiente: asalto {n:03d}")

        self.after(500, self._refrescar)

    def _al_cerrar(self) -> None:
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
