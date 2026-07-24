# Errores conocidos y diagnóstico

Problemas reales que han aparecido, su causa y cómo verificarlos. Antes de
"arreglar" algo que parece un bug, comprobar aquí si es un comportamiento
esperado o un fallo ya diagnosticado.

## Verificación con ffprobe (base)

No hay tests automatizados. Tras tocar la construcción de comandos FFmpeg, grabar
un asalto y comprobar la duración:

```
ffprobe -v error -show_entries format=duration -of csv=p=0 grabaciones/.../cam1.mkv
```

Frames reales y fps:

```
ffprobe -v error -select_streams v:0 -count_frames \
  -show_entries stream=avg_frame_rate,nb_read_frames -of csv=p=0 fichero.mkv
```

## `I/O error` al abrir una capturadora en YUV

**Síntoma:** dos de tres cámaras dan `I/O error` al iniciar en formato
`yuyv422`; en MJPEG funcionan.

**Causa raíz:** el modo pedido (resolución + fps + `pixel_format`) **no existe en
esa capturadora en YUV**. Muchas capturadoras HDMI→USB genéricas solo ofrecen
YUYV a 1080p con un tope de fps bajo (p.ej. 10), mientras que a MJPEG llegan a
30/50. Si `config.json` pide 30 fps y la capturadora solo da 10 en YUV a esa
resolución, DirectShow no encuentra el formato y aborta con `I/O error`. No es
ancho de banda ni cable: es un modo inexistente.

**Matiz importante:** dos capturadoras con el **mismo nombre** ("USB3.0 Video") e
incluso el **mismo VID:PID** pueden anunciar modos distintos según **el ancho de
banda USB negociado** en el momento de la enumeración. Colgadas de un hub o de un
puerto con poco bus, se autolimitan (p.ej. YUV 1080p a 10 fps); en un puerto USB
3.0 con ancho completo, las mismas capturadoras exponen 30/60 fps. Es decir, el
tope de fps que ves puede depender de **dónde** están conectadas, no solo del
chip.

**Diagnóstico:** listar los modos reales de cada capturadora.

```
python -c "import dispositivos, json; c=json.load(open('config.json',encoding='utf-8')); \
print(dispositivos.formatos(c['camaras'][1]['dispositivo']))"
```

Buscar las líneas `pixel_format=yuyv422 ... 1920x1080 ... max fps=N`: ese `N` es
el tope real. Si es menor que el fps de config, ese es el problema.

**Soluciones (según prioridad):**
- Conectar todas las capturadoras a puertos **USB 3.0** con ancho de banda
  suficiente (no compartir un hub saturado). En un caso real, esto hizo que
  capturadoras que topaban en YUV 10 fps pasaran a exponer 30/60.
- Si aun así el YUV no llega al fps deseado en las tres: usar **MJPEG** (formato
  común a 1080p30 en la mayoría) o **bajar resolución/fps** en YUV.

**Aviso para directo:** el YUV sin comprimir tira mucho del bus. Si en un asalto
una cámara reporta bastantes **menos frames** que las otras (ver `frames` en
`metadata.json`), es la primera señal de que el USB se queda corto: el plan B es
volver esa cámara a MJPEG.

## Mosaico `duration=N/A` y no abre

**Síntoma:** `mosaico.mkv` no se puede abrir; `ffprobe` da `duration=N/A` y a
veces `avg_frame_rate=25/1` (cuando se grabó a 30). El proceso podía además
tardar mucho o colgarse.

**Tres causas distintas, todas ya corregidas** (ver [DECISIONES.md](DECISIONES.md)):

1. **Fuente infinita.** El fondo `color=black` del filtro no tiene duración; sin
   `shortest=1` en el primer overlay, el contenedor salía sin duración y podía
   colgarse. → corregido con `shortest=1`.
2. **Framerate fantasma 25 fps.** Sin `fps=<fps>` explícito al final del filtro,
   FFmpeg inventaba 25 porque el framerate no se hereda a través del `color`
   source. → corregido pasando `fps` (de config) al filtro.
3. **Interrupción del proceso.** El mosaico corría en un hilo daemon: al cerrar
   la app, Python lo mataba y FFmpeg moría sin cerrar el MKV. → corregido
   haciéndolo proceso independiente con ventana propia + fichero `.parcial` que
   solo se renombra a `mosaico.mkv` al terminar bien.

**Verificar un mosaico:**

```
ffprobe -v error -show_entries format=duration:stream=avg_frame_rate \
  -of default grabaciones/.../mosaico.mkv
```

Debe dar duración numérica y `avg_frame_rate` igual al fps de grabación (30/1).

**Nota sobre el código de salida del `.bat`:** el `.bat` del mosaico puede
devolver código 1 por el `del` que se autoborra mientras `cmd` lo tiene abierto.
Es inofensivo: si `mosaico.mkv` existe y tiene duración, el mosaico está bien. La
app no mira ese código (proceso independiente).

## Escapado en cmd.exe (mosaico)

**Síntoma (histórico):** el mosaico fallaba al lanzarse aunque el comando FFmpeg
aislado funcionara.

**Causa:** el `filter_complex` contiene `&`, `(`, `)`, `;`, `[`, `]`, todos
metacaracteres de `cmd.exe`. Metido inline en `cmd /c "..."`, cmd partía el
comando. `subprocess.list2cmdline` no lo entrecomilla porque no tiene espacios.

**Solución:** un `.bat` temporal con **rutas absolutas** y el `filter_complex`
**entrecomillado a mano**. FFmpeg quita las comillas al parsear sus argumentos,
así que le llegan intactos. → `mosaico.py`, `generar()`.

## Falsas alarmas de "cámara caída"

**Síntoma:** una cámara se marca en rojo/ámbar durante un asalto aunque graba
bien.

**Causas posibles y protección existente:**
- Aviso benigno de FFmpeg (Fontconfig, `deprecated`...) tratado como error →
  debería estar filtrado por `_RUIDO` en `grabador.py`. Si aparece uno nuevo,
  añadirlo **con cuidado de no tapar fallos reales**.
- Parada nuestra confundida con caída → cubierto por `_detencion_pedida`.
- "SIN SEÑAL - imagen congelada" (ámbar): frames sin avanzar > 5 s. Suele ser
  **HDMI suelto real**, no falsa alarma. Verificar el cable antes de descartar.

Ver [DECISIONES.md](DECISIONES.md), sección Grabación.

## Duración grabada distinta del tiempo real

- **Todas las cámaras más cortas que el asalto:** normal. El arranque es
  secuencial (~1 s) y la parada también lleva unos segundos.
- **Duración muy superior a la real (modo prueba):** falta `-re` en la entrada
  `lavfi`. `testsrc2` estaría generando frames a velocidad de CPU.
- **Duraciones dispares entre cámaras + frames dispares:** posible pérdida de
  frames por ancho de banda USB (ver el `I/O error` de YUV arriba). Comprobar
  con `ffprobe` que los `pts_time` son continuos; si lo son, es solo desfase de
  arranque (inofensivo).

## rclone: subida fallida

`subida.py` guarda los últimos mensajes de error del log JSON de rclone. Causas
típicas del `msg` de error: sin autorizar el remoto, sin red, cuota agotada.
Recordar: se usa `copy`, nunca `sync` (ver [DECISIONES.md](DECISIONES.md)).
