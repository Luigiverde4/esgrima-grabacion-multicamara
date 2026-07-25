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

### La parada va en dos pasadas: `q` a los tres, luego esperar
`detener_asalto()` llama primero a `pedir_parada()` en las tres cámaras y solo
después a `esperar_cierre()`. La `q` es lo que fija el instante de corte, así que
enviarlas seguidas deja las tres duraciones a menos de un segundo. Haciéndolo
cámara a cámara (enviar y esperar hasta 8 s, una tras otra), cada cámara seguía
grabando mientras la anterior cerraba: en un caso real salieron 26 / 29 / 33 s.
Verificado con tres `testsrc2`: 387 frames y 12,900 s en las tres.
→ `grabador.py`, `detener_asalto()` / `pedir_parada()` / `esperar_cierre()`.

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

### El regex de `frame=` va anclado al principio de línea
`-progress` emite siempre `frame=N` al inicio de la línea. Sin el ancla `^`, un
error que mencione `frame=` (p.ej. `Error while decoding stream #0:0: frame= 12`)
se contaría como avance: la línea no llegaría a `_ultimas_lineas` y además
refrescaría `ultimo_avance`, **desactivando la detección de imagen congelada**.
Daría una cámara por buena cuando no lo está — peor que una falsa alarma.
→ `grabador.py`, `_RE_FRAME`.

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

### Relanzar una cámara caída: trozos aparte + concatenación al detener
Si una cámara se cae en directo (USB suelto), el botón **⟳ Relanzar** de su fila
la vuelve a lanzar sin cortar el asalto. El nuevo intento **no sobrescribe** lo ya
grabado: escribe `cam1_b.mkv`, `cam1_c.mkv`... y al detener el asalto se
concatenan todos en un único `cam1.mkv` con el demuxer `concat` y `-c copy`
(instantáneo, sin recodificar; MKV lo soporta sin pérdida).

La unión escribe a un fichero aparte y solo sustituye al original si FFmpeg sale
con éxito: si falla, **se conservan los trozos sueltos** — nunca se destruye
material grabado por un fallo al unir.

### El hueco de la caída se declara, no se rellena
Entre trozo y trozo, la lista del demuxer lleva una directiva `duration` con la
duración real del trozo **más** los segundos que la cámara estuvo caída (esa
directiva declara cuánto ocupa en el tiempo el fichero anterior, no la longitud
del hueco). El resultado es que el trozo siguiente entra desplazado a su posición
real y **el vídeo conserva la sincronía** con las demás cámaras; el tramo perdido
se ve en negro al reproducir.

Se declara en vez de rellenar con un vídeo negro porque no cuesta frames ni
recodificar, y el fichero acaba durando lo mismo que el asalto. Eso último es
además lo que impide que el mosaico se cuelgue con entradas de duración dispar
(ver [ERRORES_CONOCIDOS.md](ERRORES_CONOCIDOS.md)).

El hueco se mide con `time.monotonic()` entre la muerte del trozo y el
relanzamiento, y queda en `metadata.json` como `segundos_caida` (la suma de
todos).

**Admite varias caídas en el mismo asalto**, no solo una: `huecos` va en paralelo
a `trozos` y cada hueco se mide y declara por separado. El nombre del trozo lo
decide `_siguiente_libre()` buscando el primer fichero libre en disco (`_b`, `_c`,
`_d`...), no contando la lista, para que un intento fallido no pise un trozo
bueno.

Verificado con **tres caídas seguidas** en un asalto: cuatro trozos unidos en un
solo `cam1.mkv` de 42,58 s frente a 42,30 y 42,27 de las otras dos, con los
huecos situados en 6,67 s (4,49 s), 17,82 s (6,70 s) y 31,19 s (3,66 s) — todos
donde correspondía. El mosaico resultante se generó en 5 s sin avisos.

Nota: al remuxear un fichero con varios huecos, FFmpeg puede emitir avisos de
`non monotonically increasing dts` del tipo `N >= N`. Son inofensivos —no hay
timestamps duplicados en el fichero y el mosaico sale limpio—; vienen del muxer
de destino, no del MKV.
→ `grabador.py`, `_unir_trozos()` / `iniciar()` / `_duracion()`.

El botón solo se muestra mientras esa cámara esté caída con el asalto en curso.
→ `grabador.py`, `relanzar_camara()` / `_unir_trozos()` / `iniciar(intento=)`;
`app.py`, `_relanzar()`.

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

### El audio del mosaico se elige en la interfaz
El mosaico lleva **una sola** pista de audio, no la mezcla de las tres: tres
micros captando la misma sala darían eco y problemas de fase. Por defecto es la
cámara **frontal** (la del medio en `config.json`), pero el operador puede elegir
otra en el desplegable del pie del marco de cámaras — el micro mejor situado no
siempre es el del frontal.

Se guarda como `audio_mosaico` en `config.json`, con el **id** de la cámara y no
el nombre de fichero, para que siga valiendo si los nombres cambian. Si la cámara
guardada ya no existe, se cae a la frontal.

El mapeo usa `-map <idx>:a?` — la `?` hace que el mosaico salga mudo en vez de
fallar si esa cámara se grabó sin micro. Ojo con el efecto secundario: **si la
elegida no tiene audio, no se coge de otra**. Por eso la interfaz avisa al elegir
una cámara sin micro asignado.
→ `mosaico.py`, `generar()`; `app.py`, `_elegir_audio_mosaico()`;
`grabador.py`, `audio_mosaico` / `guardar_audio_mosaico()`.

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

### El contador solo puede subir, y el desajuste se avisa
`siguiente_numero()` toma el **mayor** entre `ultimo_asalto` y lo grabado en
disco. Consecuencia: bajar el contador a mano en `config.json` **no renumera
nada** mientras queden grabaciones más altas; para renumerar hay que archivar
antes esas jornadas. Cuando el disco manda se anota en `Sesion.contador_ignorado`
y la interfaz lo registra en el log (una vez por desajuste, no en cada refresco):
en silencio, un contador ignorado es indistinguible de un fallo al guardar.
→ `grabador.py`, `siguiente_numero()`; `app.py`, `_avisar_contador_ignorado()`.

### `_maximo_en_disco()` exige metadata.json
Es la red de seguridad por si `config.json` se pierde. Exige tres condiciones y
las tres importan: jornada válida (`_RE_JORNADA`), nombre `NNN` (`_RE_ASALTO`) y
**presencia de `metadata.json`**. Sin la última, una carpeta creada a mano como
`500_revisar` dispararía el contador a 501.
→ `grabador.py`, `_maximo_en_disco()`. Ver [FLUJO_DE_TRABAJO.md](FLUJO_DE_TRABAJO.md).
