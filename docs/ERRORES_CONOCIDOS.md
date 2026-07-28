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

## Mosaico colgado tras relanzar una cámara (duraciones dispares)

**Síntoma:** el mosaico no termina nunca. La ventana se queda parada, queda un
`mosaico.parcial.mkv` que **no crece** (clavado en ~1,3 MB) y un `ffmpeg.exe`
acumulando más de 1 GB de RAM. Pasa solo en asaltos donde se relanzó una cámara.

**Causa:** al relanzar, esa cámara pierde el tramo que estuvo caída y su vídeo
queda **más corto** que los otros dos (caso real: 64,7 s frente a 77,6 s). Los
overlays de los laterales esperaban frames que ya no llegaban, y como el fondo
`color` es una fuente infinita, el grafo no terminaba nunca.

**Corregido** con `eof_action=pass` en los tres overlays: al agotarse una
entrada se sigue con lo que haya debajo en vez de bloquear. → `mosaico.py`,
`_filtro()`.

**Verificado:** los mismos ficheros que llevaban media hora colgados generaron el
mosaico completo (77,600 s, 30 fps, audio incluido) sin bloqueo.

**Si te encuentras uno colgado:** matar el `ffmpeg.exe`, borrar
`mosaico.parcial.mkv` y `_mosaico.bat`, y volver a lanzarlo. Las grabaciones
`camN.mkv` **no están afectadas** — el mosaico es un proceso aparte que solo lee.

## Escapado en cmd.exe (mosaico)

**Síntoma (histórico):** el mosaico fallaba al lanzarse aunque el comando FFmpeg
aislado funcionara.

**Causa:** el `filter_complex` contiene `&`, `(`, `)`, `;`, `[`, `]`, todos
metacaracteres de `cmd.exe`. Metido inline en `cmd /c "..."`, cmd partía el
comando. `subprocess.list2cmdline` no lo entrecomilla porque no tiene espacios.

**Solución:** un `.bat` temporal con **rutas absolutas** y el `filter_complex`
**entrecomillado a mano**. FFmpeg quita las comillas al parsear sus argumentos,
así que le llegan intactos. → `mosaico.py`, `generar()`.

## `real-time buffer ... too full` — las tres cámaras marcadas como fallidas

**Síntoma:** al terminar el asalto salta "3 de 3 cámaras han fallado" con un
mensaje `[dshow @ ...] real-time buffer [...] too full or near too full (...),
frame dropped!`. Pero los vídeos se reproducen bien.

**Causa:** es un **aviso, no un fallo**. dshow avisa de que se le llenó el búfer
de entrada (`-rtbufsize`) y descartó algún frame; FFmpeg sigue grabando y el
fichero queda correcto. No estaba en `_RUIDO`, así que se guardaba como error de
cámara. → corregido añadiendo `real-time buffer` a `_RUIDO` en `grabador.py`.

**Pero comprobar los frames igualmente:** el aviso sí indica pérdida real de
frames. En un caso real (asalto 044, `yuyv422` a 1080p30): 37,1 s deberían dar
~1110 frames por cámara y dieron 756 / 786 / 842 — dispares entre sí y muy por
debajo. Eso es el USB quedándose corto con YUV sin comprimir, no un problema de
software. Ver el `I/O error` de YUV más arriba: la solución es puertos USB 3.0
con ancho suficiente, o pasar esas cámaras a **MJPEG**.

Regla: el aviso ya no marca la cámara en rojo, pero si aparece, mirar `frames` en
`metadata.json` antes del siguiente asalto.

## `[dshow @ ...] frame=1649` — cámara marcada como fallida y **mosaico no generado**

**Síntoma:** al terminar el asalto salta "2 de 3 cámaras han fallado" con un
error de una sola línea con esta pinta:

```
! cam3: [dshow @ 000001f6b27ae440] frame=1649
```

y además **no se genera el mosaico**, sin que aparezca ningún aviso en el log
explicando por qué. Los tres vídeos se reproducen bien.

**Causa:** es **telemetría de `-progress` con el prefijo de módulo de dshow
pegado por delante**. stderr no está sincronizado entre demuxer y muxer, así que
un aviso de dshow (típicamente el `real-time buffer` de aquí arriba) y la línea
`frame=N` se entrelazan y salen fundidos en una sola línea.

Esa línea no casaba con nada:
- `_RE_FRAME` está anclado a inicio de línea (`^frame=`) **a propósito**, así que
  el `frame=` precedido del prefijo no contaba como avance;
- no empieza por ninguno de los prefijos de `_TELEMETRIA`;
- no contenía ninguna cadena de `_RUIDO`.

Al no ser ninguna de las tres cosas, se guardaba como error real y la cámara se
declaraba caída al detener, con `intentos: 1` y `segundos_caida: 0.0` — es decir,
nunca se cayó de verdad.

**El efecto en cadena es lo grave:** `_generar_mosaico()` (en `app.py`) solo
genera el mosaico si las tres cámaras grabaron bien, y sale por un `return`
temprano **sin escribir nada en el log**. Un falso positivo en una cámara se
convierte, silenciosamente, en un asalto sin mosaico.

**Corregido** en `grabador.py` con `_RE_TELEMETRIA_PREFIJADA`, comprobada desde
`_es_ruido()`: descarta líneas cuyo contenido, quitado el prefijo `[modulo @
dirección]`, es telemetría conocida. No tapa fallos reales — `I/O error`,
`Could not run graph`, `Failed to set video format` llevan el mismo prefijo y
siguen registrándose, porque lo que se compara es lo que va *después*.

**Caso real:** asalto 028 del 2026-07-27. cam3 marcada como fallida; los tres
`.mkv` intactos (95,1 / 82,3 / 93,5 s, ninguno `N/A`). Mosaico regenerado a mano
después con `mosaico.generar()`.

**Si vuelve a pasar con otro prefijo de módulo:** regenerar el mosaico del asalto
sin perder la prueba, con los nombres de fichero en el orden que toque:

```python
python -c "from pathlib import Path; import mosaico; \
mosaico.generar(Path('grabaciones/JORNADA/ASALTO'), frontal='cam1.mkv', \
izquierda='cam2.mkv', derecha='cam3.mkv', fps=30, audio_de='cam1.mkv')"
```

## Falsas alarmas de "cámara caída"

**Síntoma:** una cámara se marca en rojo/ámbar durante un asalto aunque graba
bien.

**Causas posibles y protección existente:**
- Aviso benigno de FFmpeg (Fontconfig, `deprecated`...) tratado como error →
  debería estar filtrado por `_RUIDO` en `grabador.py`. Si aparece uno nuevo,
  añadirlo **con cuidado de no tapar fallos reales**.
- Telemetría con el prefijo de módulo pegado delante (`[dshow @ ...] frame=N`) →
  filtrada por `_RE_TELEMETRIA_PREFIJADA`. Ver la sección propia más arriba:
  el efecto secundario es que **el asalto se queda sin mosaico en silencio**.
- Parada nuestra confundida con caída → cubierto por `_detencion_pedida`.
- "SIN SEÑAL - imagen congelada" (ámbar): frames sin avanzar > 2 s
  (`UMBRAL_CONGELADA_S`). Suele ser **HDMI suelto real**, no falsa alarma.
  Verificar el cable antes de descartar.

  Si aparece de forma intermitente en cámaras que **siguen grabando bien** (el
  contador de frames se reanuda solo y el `.mkv` sale correcto), es que el
  umbral se ha quedado corto para ese hardware: revisar el hueco real entre
  informes antes de tocar nada, y ver el apartado del umbral en
  [DECISIONES.md](DECISIONES.md). No relanzar por un ámbar que se apaga solo.

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

### Comprobación tras un asalto con cámara relanzada

Cuando se ha pulsado **⟳ Relanzar** durante un asalto, esa cámara se grabó en
trozos que luego se unieron con negro en medio. Merece la pena comprobar que la
unión cuadró — **no hace falta entender el código**, basta comparar duraciones:

```
for %f in (cam1 cam2 cam3) do @ffprobe -v error -show_entries format=duration -of csv=p=0 "grabaciones\JORNADA\ASALTO\%f.mkv"
```

**Las tres deben parecerse (menos de ~1 s de diferencia).** Si la cámara
relanzada sale **más larga** que las sanas, el hueco negro se calculó de más;
si sale **más corta**, de menos. En ambos casos el mosaico quedará descuadrado
y conviene revisarlo antes de seguir. Ver la entrada de `duracion_real()` en
[DECISIONES.md](DECISIONES.md), que es el fallo que producía justo eso.

## rclone: subida fallida

`subida.py` guarda los últimos mensajes de error del log JSON de rclone. Causas
típicas del `msg` de error: sin autorizar el remoto, sin red, cuota agotada.
Recordar: se usa `copy`, nunca `sync` (ver [DECISIONES.md](DECISIONES.md)).
