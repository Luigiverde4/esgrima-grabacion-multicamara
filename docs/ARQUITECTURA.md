# Arquitectura

Aplicación de escritorio (Windows) para grabar asaltos de esgrima con tres
cámaras simultáneas, generar un mosaico de los tres POVs y subirlo todo a la nube.
Sin dependencias externas de Python: solo stdlib (Tkinter incluido). Sí requiere
`ffmpeg`, `ffprobe`, `ffplay` y `rclone` en el PATH.

## Módulos

Seis módulos, con la GUI desacoplada del motor.

| Módulo | Rol | Sabe de Tkinter |
|---|---|---|
| `ffmpeg_utils.py` | Utilidades compartidas de FFmpeg: banderas de consola (`SIN_VENTANA`, `NUEVA_CONSOLA`) y consultas de `ffprobe` (`duracion`, `muestra_audio`, `tiene_audio`). Sin dependencias del resto. | No |
| `grabador.py` | Motor. `Sesion` coordina el conjunto; `GrabadorCamara` envuelve un proceso FFmpeg y publica su estado en `EstadoCamara`. Numera los asaltos y persiste `config.json`. | No |
| `dispositivos.py` | Enumeración DirectShow (vídeo y audio) vía FFmpeg, emparejado micro↔cámara y previsualización con `ffplay`. | No |
| `mosaico.py` | Genera los mosaicos (1920x1080) en procesos FFmpeg independientes con ventana propia: `generar()` con los tres POVs y `generar_dos()` con solo las dos laterales. | No |
| `subida.py` | `rclone copy` en un hilo, con callbacks de progreso (JSON). | No |
| `app.py` | GUI Tkinter. Sondea `EstadoCamara` cada 500 ms para pintar los semáforos; recibe callbacks de la subida. Punto de entrada. | Sí |

`ffmpeg_utils.py` es la base: lo importan los otros cuatro módulos del motor y no
importa ninguno, así que no puede haber ciclos. Contiene **utilidades**, no
lógica de dominio: los comandos de FFmpeg de cada módulo (`grabador.comando()`,
`mosaico._filtro()`, `dispositivos.listar_*`) se quedan donde están, porque
llevan invariantes que se entienden en su contexto.

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

## Dos mosaicos por asalto

Al terminar un asalto se lanzan **dos mosaicos independientes**, cada uno a su
fichero y a la vez (`app.py`, `_generar_mosaico()`):

| Función | Contenido | Fichero | Condición |
|---|---|---|---|
| `generar()` | Tres POVs: frontal grande arriba, laterales abajo | `_M.mkv` | Las **tres** cámaras grabaron bien |
| `generar_dos()` | Solo las dos laterales, lado a lado (960x540 centradas) | `_M2.mkv` | Las **dos laterales** grabaron bien |

El de dos existe porque la cámara central es la que más se cae en pista. El de
tres se omite entero si falla cualquier cámara, así que sin el de dos un asalto
con la central caída se quedaba **sin ninguna vista compuesta** pese a tener dos
POVs perfectamente válidos. No se sustituyen: cuando todo va bien se generan los
dos.

Ambos comparten `_lanzar()`, que construye el `.bat` y lo lanza; lo único que
cambia entre ellos es cuántas entradas hay y qué filtro las combina. Pueden
correr simultáneamente porque tanto el `.parcial` como el `.bat` se derivan del
**nombre de salida**, distinto en cada uno.

Para asaltos ya grabados hay un script suelto, `mosaico_laterales.py`, que
recorre una carpeta de asalto o de jornada y genera los `_M2.mkv` que falten
(con `--secuencial` para no competir por CPU si la app está grabando).

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
    12_47_ID_027/              asalto (TIRADOR1_TIRADOR2_ID_NNN)
      12_47_A.mkv              lateral izquierda (cam1)
      12_47_B.mkv              frontal           (cam2)
      12_47_C.mkv              lateral derecha   (cam3)
      12_47_M.mkv              tres POVs compuestos
      12_47_M2.mkv             solo las dos laterales
      metadata.json            registro del asalto (lo escribe la app al cerrar)
```

Sin los dos números de tirador, los ficheros salen como `cam1.mkv`, `cam2.mkv`,
`cam3.mkv`, `mosaico.mkv` y `mosaico2.mkv`. Ver [CONVENCIONES.md](CONVENCIONES.md).

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
