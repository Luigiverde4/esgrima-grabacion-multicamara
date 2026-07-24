# Decisiones e invariantes

Estas decisiones tienen motivo y revertirlas causa fallos difíciles de ver. Cada
una lleva **qué**, **por qué** y **dónde** en el código. Antes de cambiar
cualquiera, entender el porqué.

## Contexto de despliegue

Se usa **en directo durante una competición**, sobre Windows y con un operador
que puede no ser el desarrollador. Prioridades, en orden:

1. **No perder una grabación.**
2. **Hacer visible cualquier fallo al instante.**
3. **Evitar falsas alarmas.** Un aviso espurio de "cámara caída" durante un
   asalto es un fallo grave, no un detalle.

Todas las decisiones de abajo se justifican contra estas tres prioridades.

## Grabación

### Contenedor MKV, no MP4
Un corte de corriente deja un MP4 corrupto e irrecuperable; el MKV se reproduce
hasta donde llegó. El contenedor lo decide la extensión `.mkv` del destino.
→ `grabador.py`, `comando()`.

### Detener FFmpeg escribiendo `q` en stdin, no con `terminate()`/`kill()`
Solo el cierre limpio escribe la duración en el contenedor. Por eso el proceso se
lanza con `stdin=PIPE` y **sin** `-nostdin`. Cascada de tres intentos:
`q` (8 s) → `terminate()` (5 s) → `kill()`. `terminate()` produce fichero
reproducible pero sin duración; queda como plan B por timeout.
→ `grabador.py`, `detener()`.

### `_detencion_pedida` distingue parada nuestra de caída real
FFmpeg sale con código distinto de cero de forma legítima cuando lo paramos
nosotros. Sin esta bandera, cada parada normal se marcaría como fallo. Se activa
**antes** de tocar el proceso, porque el hilo lector puede despertar en cuanto
FFmpeg muera.
→ `grabador.py`, `detener()` / `_leer_progreso()`.

### Filtrado de ruido de FFmpeg (`_RUIDO`)
Avisos benignos (Fontconfig, `deprecated`, `non-monotonic`...) marcarían cámaras
como caídas en mitad de la competición. Al añadir entradas a esa lista,
comprobar que **no tapan un fallo real**: una cámara marcada como correcta cuando
no graba es peor que una falsa alarma.
→ `grabador.py`, `_RUIDO` / `_es_ruido()`.

### Detección de imagen congelada por contador de frames
No basta con mirar si el proceso vive: cuando se afloja el HDMI, FFmpeg sigue
corriendo y el fichero sigue creciendo, pero la imagen se queda quieta. Lo que lo
delata es que el contador de frames deja de avanzar (`EstadoCamara.bloqueada`,
umbral 5 s). Se exige `frames > 0` para no dar la alarma durante el arranque.
→ `grabador.py`, `EstadoCamara.bloqueada`.

### Arranque secuencial de las tres cámaras
Se lanzan de una en una (~1 s de desfase total). Consecuencia: los tres POV **no
arrancan al mismo instante**. Aceptable para revisión técnica; para montaje
sincronizado al frame haría falta una claqueta/palmada de referencia al inicio.
→ `grabador.py`, `iniciar_asalto()`.

## Formato de entrada y dispositivos

### Formato por defecto MJPEG (no YUYV crudo)
Por defecto FFmpeg coge YUYV sin comprimir (~1.5 Gbps a 1080p): varias
capturadoras así saturan el USB. Pidiendo MJPEG explícitamente entra comprimido y
caben más. El operador puede forzar `yuyv422` o `auto` por cámara.
→ `grabador.py`, `_entrada()`. Ver también [ERRORES_CONOCIDOS.md](ERRORES_CONOCIDOS.md).

### `-re` en las entradas `lavfi` (modo prueba)
Sin él, `testsrc2` genera frames a velocidad de CPU y las duraciones grabadas no
se corresponden con el tiempo real.
→ `grabador.py`, `_entrada()`.

### `-af aresample=async=1` al grabar audio dshow
El reloj del audio HDMI puede ir a distinta velocidad que el del vídeo; sin
resampleo asíncrono, la sincronía deriva a lo largo del asalto.
→ `grabador.py`, `comando()`.

### Una capturadora no admite dos procesos DirectShow a la vez
Por eso la previsualización (`ffplay`) se cierra al iniciar un asalto
(`_cerrar_previews()` en `_iniciar()`) y el botón "Ver" se desactiva mientras se
graba. Si se abre otra vía de acceso al dispositivo, respetar esta exclusión.
→ `app.py`, `_iniciar()` / `_refrescar()`.

### Emparejar micro por el sufijo `(<vídeo>)`, no por "contiene"
Las capturadoras exponen su micro como `Microphone (USB Video #2)`. Buscar por
"contiene el nombre" cogería el micro equivocado con capturadoras idénticas
(`USB Video` es subcadena de `USB Video #2`). Se empareja por sufijo exacto y, en
segundo intento, por raíz común; si hay ambigüedad se devuelve `None` (mejor que
el operador elija a mano que arriesgar un cruce).
→ `dispositivos.py`, `emparejar_audio()`.

### Los objetos `Camara` son la fuente de verdad al persistir
`_persistir_camaras()` reconstruye el bloque `camaras` de `config.json` desde
ellos, no al revés, para que vídeo y audio nunca se desincronicen.
→ `grabador.py`, `_persistir_camaras()`. Ver [CONVENCIONES.md](CONVENCIONES.md).

## Mosaico

### El mosaico es un proceso independiente, no un hilo daemon
Recodificar tres 1080p tarda más que el asalto. Antes corría en un hilo daemon;
al cerrar la app, Python mataba el hilo y FFmpeg moría sin cerrar el MKV →
`duration=N/A`, fichero que no abre. Ahora es un proceso propio con ventana que
sobrevive al cierre de la app.
→ `mosaico.py`, `generar()`. Ver [ERRORES_CONOCIDOS.md](ERRORES_CONOCIDOS.md).

### `shortest=1` en el primer overlay del mosaico
El fondo `color=black` es una **fuente infinita**. Sin `shortest=1` el mosaico
sale sin duración (`ffprobe: N/A`) y puede colgarse esperando frames que no
llegan.
→ `mosaico.py`, `_filtro()`.

### `fps=<fps>` explícito al final del filtro
Sin fijarlo, FFmpeg inventa 25 fps aunque las entradas sean a 30, porque el
framerate no se hereda bien a través del `color` source. El fps sale de
`config.json` para casar con la grabación.
→ `mosaico.py`, `_filtro()` (parámetro `fps`).

### `mosaico.parcial.mkv` renombrado al final vía `.bat`
FFmpeg escribe el `.parcial`; solo si termina bien se renombra a `mosaico.mkv`.
Una interrupción nunca deja un `mosaico.mkv` corrupto que parezca válido. Se usa
un `.bat` temporal (no `cmd /c "una línea"`) porque el `filter_complex` contiene
`&`, `()`, `;`, `[]` — metacaracteres de `cmd.exe` — y meterlo inline exige un
escapado frágil. Rutas absolutas y filtro entrecomillado.
→ `mosaico.py`, `generar()`. Ver [ERRORES_CONOCIDOS.md](ERRORES_CONOCIDOS.md).

## Subida

### `rclone copy`, nunca `sync`
`sync` borraría en OneDrive todo lo que no exista en local — destruiría
grabaciones ya subidas.
→ `subida.py`, `subir()`.

### `--use-json-log`, no `--stats-one-line`
El formato legible no emite saltos de línea, así que leerlo línea a línea daba un
progreso a trompicones. El JSON trae además `transferring[].name`, que permite
mostrar qué asalto se está subiendo.
→ `subida.py`, docstring y `tarea()`.

### `--min-age 30s`
Nunca subir un asalto que todavía se está grabando: rclone falla al copiar un
fichero que crece bajo sus pies. La GUI ya bloquea el botón, pero `--min-age`
cubre una segunda instancia o una subida lanzada desde fuera.
→ `subida.py`, `tarea()`.

## Numeración de asaltos

### El contador vive en config.json y se escribe al INICIAR
`ultimo_asalto` se guarda al arrancar el asalto, no al terminarlo: si la app
muere a mitad, el número queda reservado y no se reutiliza (no se sobrescribe una
grabación parcial).
→ `grabador.py`, `iniciar_asalto()` / `_guardar_contador()`.

### `_maximo_en_disco()` exige metadata.json
Es la red de seguridad por si `config.json` se pierde. Exige tres condiciones y
las tres importan: jornada válida (`_RE_JORNADA`), nombre `NNN` (`_RE_ASALTO`) y
**presencia de `metadata.json`**. Sin la última, una carpeta creada a mano como
`500_revisar` dispararía el contador a 501.
→ `grabador.py`, `_maximo_en_disco()`. Ver [FLUJO_DE_TRABAJO.md](FLUJO_DE_TRABAJO.md).
