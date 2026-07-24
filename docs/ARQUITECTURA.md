# Arquitectura

Aplicación de escritorio (Windows) para grabar asaltos de esgrima con tres
cámaras simultáneas, generar un mosaico de los tres POVs y subir todo a OneDrive.
Sin dependencias externas de Python: solo stdlib (Tkinter incluido). Sí requiere
`ffmpeg`, `ffprobe`, `ffplay` y `rclone` en el PATH.

## Módulos

Cinco módulos, con la GUI desacoplada del motor.

| Módulo | Rol | Sabe de Tkinter |
|---|---|---|
| `grabador.py` | Motor. `Sesion` coordina el conjunto; `GrabadorCamara` envuelve un proceso FFmpeg y publica su estado en `EstadoCamara`. Numera los asaltos y persiste `config.json`. | No |
| `dispositivos.py` | Enumeración DirectShow (vídeo y audio) vía FFmpeg, emparejado micro↔cámara y previsualización con `ffplay`. | No |
| `mosaico.py` | Genera `mosaico.mkv` (1920x1080, tres POVs) en un proceso FFmpeg independiente con ventana propia. | No |
| `subida.py` | `rclone copy` en un hilo, con callbacks de progreso (JSON). | No |
| `app.py` | GUI Tkinter. Sondea `EstadoCamara` cada 500 ms para pintar los semáforos; recibe callbacks de la subida. Punto de entrada. | Sí |

El motor no importa Tkinter ni sabe que existe una interfaz: se podría controlar
desde una web o un pedal sin tocar `grabador.py`.

## Un proceso FFmpeg por cámara

**Un proceso FFmpeg independiente por cámara**, no uno con varias entradas. Si
una capturadora se desconecta, las demás siguen grabando. Un único proceso sería
un único punto de fallo: al caer una capturadora, FFmpeg abortaría y se perderían
los tres POVs en vez de uno. En un evento irrepetible eso lo justifica.

El precio es que los tres arrancan escalonados (~1 s cada uno, arranque
secuencial en `Sesion.iniciar_asalto`). Irrelevante para revisión técnica; para
montaje sincronizado al frame haría falta claqueta. Ver [DECISIONES.md](DECISIONES.md).

Cada `GrabadorCamara` lanza además un hilo que lee el stderr de FFmpeg
(`-progress pipe:2`) para contar frames y detectar errores.

## Cómo se comunican motor e interfaz

Dos vías distintas, cada una por un motivo:

- **Grabación → por sondeo.** Cada `GrabadorCamara` escribe en su `EstadoCamara`
  y `app.py` lo consulta en `_refrescar()` cada 500 ms. No hay callbacks del
  motor hacia la GUI. Motivo: Tkinter no admite que se toquen sus widgets desde
  otro hilo, y aquí hay un hilo por cámara leyendo FFmpeg. Sondear evita el
  problema de raíz y mantiene el motor sin saber de la GUI.

- **Subida → por callback.** `rclone` corre en un hilo y avisa del progreso.
  Esos avisos se encolan con `self.after(0, ...)` para ejecutarse en el hilo de
  Tkinter. Tocar widgets directamente desde el hilo de rclone cuelga la ventana
  de forma intermitente.

- **Mosaico → sin comunicación.** Es un proceso independiente con ventana propia
  (ver abajo). No devuelve nada a la GUI; su estado se ve en su ventana.

## El mosaico como proceso independiente

`mosaico.generar()` lanza FFmpeg en un **proceso propio con consola visible**
(`CREATE_NEW_CONSOLE`), no en un hilo de la app. Motivos:

- Recodificar tres 1080p tarda **más que el propio asalto**; no puede bloquear
  la interfaz entre asaltos.
- Al ser proceso propio (no hilo daemon), **sobrevive al cierre de la app**: si
  el operador cierra la aplicación con un mosaico a medias, el mosaico termina
  igualmente en su ventana.
- La ventana muestra el progreso de FFmpeg y se cierra sola al terminar.

Escribe en `mosaico.parcial.mkv` y solo al terminar bien lo renombra a
`mosaico.mkv`, vía un `.bat` temporal autogenerado. Así una interrupción nunca
deja un `mosaico.mkv` corrupto que parezca válido. Ver [DECISIONES.md](DECISIONES.md)
y [ERRORES_CONOCIDOS.md](ERRORES_CONOCIDOS.md).

## Estructura de datos en disco

```
grabaciones/
  MIERCOLES_22/                jornada (DIA_NN)
    007_Garcia_Lopez/          asalto (ID_NOMBRE1_NOMBRE2)
      cam1.mkv                 lateral izquierda
      cam2.mkv                 frontal
      cam3.mkv                 lateral derecha
      mosaico.mkv              tres POVs compuestos
      metadata.json            registro del asalto (lo escribe la app al cerrar)
```

El orden de cámaras `[cam1, cam2, cam3]` = `[izquierda, frontal, derecha]` es el
mismo en `config.json`, en `self.sesion.camaras`, en `self.sesion.grabadores` y
en `self.filas` de la GUI: `_refrescar()` los empareja con `zip()`. Al reordenar
cámaras, revisar los cuatro sitios.

## Ficheros del repositorio

- `config.json` — configuración y estado persistente (cámaras, contador de
  asaltos, destino rclone, ajustes de vídeo). Ver [CONVENCIONES.md](CONVENCIONES.md).

**Pendiente (no implementado):** previsualización en vivo simultánea de los tres
POVs empotrada en la interfaz. Requeriría MediaMTX, pero **no hay binario en el
repositorio hoy**. La previsualización actual abre una ventana `ffplay` por
cámara (ver `dispositivos.previsualizar`).
