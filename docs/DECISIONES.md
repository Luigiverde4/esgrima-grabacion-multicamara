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
→ `grabador.py`, `pedir_parada()` / `esperar_cierre()`.

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
→ `grabador.py`, `pedir_parada()` / `_leer_progreso()`.

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
delata es que el contador de frames deja de avanzar (`EstadoCamara.bloqueada`).
Se exige `frames > 0` para no dar la alarma durante el arranque.
→ `grabador.py`, `EstadoCamara.bloqueada`.

### El umbral de congelada son 2 s (`UMBRAL_CONGELADA_S`)
FFmpeg informa cada 0,5 s (`-stats_period`), así que 2 s son **tres informes
perdidos**. El valor sale de medir el hueco real entre informes con tres cámaras
1080p: **0,52 s** en reposo y **0,64 s** con las 16 CPU saturadas — ninguna falsa
alarma en ninguno de los dos casos.

Estuvo en 5 s (diez informes: demasiado lento en directo) y en 1 s. **No bajarlo
a 1 s sin volver a medir con capturadoras reales**: las medidas de arriba usan
`testsrc2`, que no toca USB ni DirectShow. En `yuyv422` a 1080p el bus va justo
— es el escenario que produce el aviso `real-time buffer too full` de `_RUIDO`,
que ya marcó las tres cámaras como caídas en un asalto perfectamente válido.

El equilibrio es asimétrico y por eso se elige el lado conservador: detectar 1 s
más tarde cuesta 1 s de vídeo congelado, mientras que una falsa alarma empuja al
operador a relanzar, y **relanzar sin motivo sí destruye valor** (corta el trozo
e inserta un hueco negro en una grabación que estaba sana). Prioridad 3.
→ `grabador.py`, `EstadoCamara.UMBRAL_CONGELADA_S`.

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

### Al unir trozos se mide con `duracion_real()`, no con `duracion()`
`duracion()` lee la cabecera del contenedor: es instantáneo, pero devuelve `0.0`
cuando el fichero se cortó sin cerrar (`ffprobe` informa `N/A`). Ese trozo **sí
tiene vídeo**; solo le falta el dato en la cabecera.

Para el uso original ese `0.0` era correcto ("no declarar hueco, que es lo
prudente"). Pero `_unir_trozos()` lo usa en otra cuenta —
`falta = referencia − grabado`— donde `0.0` no significa "no declares nada" sino
**"este trozo no dura nada"**, e infla el negro justo en los segundos que el
trozo sí duraba. Un valor neutro correcto en un sitio, usado donde no lo era.

Medido: cámara caída con el proceso muerto de golpe, trozos de 0 s (mal cerrado)
+ 6,7 s. `grabado` salía 6,7 en vez de ~10, `falta` 8,77 en vez de ~4,8, y el
fichero final duraba **18,93 s frente a 15,47** de las sanas. Con
`duracion_real()`: 15,43 / 15,47 / 15,47.

**Por qué no se había visto:** depende de cómo muera FFmpeg. Con un USB suelto
—el caso habitual— FFmpeg detecta el error y cierra el MKV ordenadamente, la
duración queda escrita y el cálculo sale bien. Solo falla cuando el proceso muere
sin cerrar: corte de corriente del hub, cuelgue, o la rama `kill()` de
`esperar_cierre()`. Además el síntoma son unos segundos de negro de más al final
de una sola cámara: invisible sin comparar las tres duraciones con `ffprobe`.
→ `ffmpeg_utils.py`, `duracion_real()`; `grabador.py`, `_duracion`.

### El hueco de la caída se rellena con negro real
Entre trozo y trozo se genera un segmento temporal negro con los mismos
parametros de video, y audio en silencio si la camara lo llevaba. El demuxer
concat une ese tramo negro con `-c copy`, asi que el video CONSERVA LA SINCRONIA
con las demas camaras y el hueco se ve negro de verdad al reproducir, no como un
salto de timestamps.
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
donde correspondía, y mostrando negro real en los tramos de recuperacion. El
mosaico resultante se generó en 5 s sin avisos.

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

### `NUEVA_CONSOLA` es solo para el mosaico; `ffprobe` va siempre `SIN_VENTANA`
La ventana de consola del mosaico **es deliberada y se conserva**: es donde
FFmpeg pinta su progreso y donde el operador ve si se ha atascado.

Lo que no debe llevarla es `ffprobe`. Sus consultas son instantáneas y no pintan
nada, así que abrirles una consola solo producía **4 ventanas negras parpadeando**
antes de la ventana útil (una por `tiene_audio` y tres por `duracion`). Venía de
copiar la constante del módulo en funciones que se trajeron del grabador.

Ahora ambas constantes viven en `ffmpeg_utils.py` y las consultas usan
`SIN_VENTANA` internamente, así que el error no se puede repetir por descuido.
→ `ffmpeg_utils.py`, `SIN_VENTANA` / `NUEVA_CONSOLA`; `mosaico.py`, `generar()`.

### `ffmpeg_utils.py` centraliza utilidades, no lógica de dominio
Las banderas de consola estaban copiadas literalmente en cuatro módulos y
`_duracion()` en dos, ya con diferencias entre copias. Se unifican en
`ffmpeg_utils.py`, que no importa a nadie del proyecto (imposible crear ciclos).

**Lo que NO se movió, a propósito:** `grabador.comando()`, `mosaico._filtro()` y
`dispositivos.listar_*`. Construyen las líneas de FFmpeg de cada módulo y llevan
invariantes documentadas aquí; sacarlas de su contexto las haría más difíciles de
entender. La modularidad útil era extraer lo compartido, no trocear el dominio.
→ `ffmpeg_utils.py`.

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

### El `.bat` se cierra con `endlocal & (goto) 2>nul & (del … & exit /b %CODIGO%)`
Esa línea final parece un jeroglífico pero cada parte hace falta, y está medida
con las cuatro combinaciones (éxito/fallo × borrado/no borrado).

Un `del "<el propio .bat>"` a secas **hace que el proceso salga siempre con
código 1**, incluso cuando el mosaico se ha generado perfectamente: `cmd` lee el
fichero por líneas conforme avanza, y al borrarlo intenta leer la siguiente línea
de un fichero que ya no existe (`The batch file cannot be found`).

Era un fallo latente: `app.py` solo mira si `generar()` devuelve `None`, así que
no se notaba. Pero cualquiera que añadiese un `if proc.wait() == 0` para avisar de
mosaicos fallidos habría visto fallo en el 100 % de los mosaicos correctos —
justo lo contrario de la prioridad 2.

- `(goto) 2>nul` cierra el contexto del batch **antes** de borrarlo, para que
  `cmd` no vuelva a leer el fichero.
- `%CODIGO%` se guarda con `set` tras FFmpeg y tras el `move`, porque
  `if errorlevel` no lo conserva: sin guardarlo, el código que sale es el del
  último comando ejecutado (el propio `del`), no el de FFmpeg.
- `exit /b %CODIGO%` lo devuelve.

Verificado: éxito → `0` con `mosaico.mkv` renombrado; entrada corrupta → código
distinto de 0 **sin** renombrar; el `.bat` se borra en ambos casos.
→ `mosaico.py`, `generar()`.

### La cámara de arriba del mosaico se elige en la interfaz
Qué POV va grande arriba lo decide el operador en el desplegable "Mosaico ·
arriba"; las otras dos se reparten la fila de abajo **conservando el orden de
`config.json`**, para que el mosaico siga leyéndose de izquierda a derecha.

Se guarda como `frontal_mosaico` en `config.json` (el **id** de la cámara, no el
fichero). Si la guardada no está entre las grabadas, se cae a la del medio.
Es independiente del audio: cambiar una cosa no toca la otra.
→ `app.py`, `_generar_mosaico()` / `_elegir_frontal_mosaico()`;
`grabador.py`, `frontal_mosaico` / `guardar_frontal_mosaico()`.

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
`sync` borraría en el destino todo lo que no exista en local — destruiría
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

### `config.json` es el punto único de control de la numeración
`siguiente_numero()` devuelve `ultimo_asalto + 1` y **no mira el disco**. El
contador se corrige o se reinicia editando un número en un fichero, y nada más;
renombrar o mover carpetas no lo altera en ningún sentido.

Antes se tomaba el **mayor** entre el contador y un escaneo del disco
(`_maximo_en_disco()`, eliminado). Se quitó porque hacía el contador
unidireccional: bajarlo a mano no renumeraba nada mientras quedaran grabaciones
más altas, lo que obligaba a archivar jornadas para reiniciar y era difícil de
explicar al operador.

Contrapartida asumida: si `config.json` se pierde o se restaura una copia
antigua, el contador retrocede. Contra eso protege la comprobación de colisión
de abajo, que es lo que garantiza la prioridad 1 (no perder una grabación).
→ `grabador.py`, `siguiente_numero()`.

### Nunca se graba dentro de una carpeta que ya tiene material
`iniciar_asalto()` comprueba si el número calculado ya existe en la jornada y,
si existe, avanza al primero libre. Sin esta comprobación un contador atrasado
sería destructivo en silencio: `mkdir(exist_ok=True)` no falla, se grabaría
dentro de la carpeta existente y los `.mkv` anteriores se sobrescribirían.

La colisión se busca por el **ID del final del nombre**, no por el nombre
completo: `12_47_ID_027` y `3_9_ID_027` son el mismo asalto con distintos
tiradores y colisionan igual.

Solo cuenta como ocupada si hay **`metadata.json`**, que únicamente escribe esta
aplicación al terminar de grabar. Una carpeta vacía (arranque fallido, o creada
a mano) no bloquea el número ni deja huecos en la numeración.

El salto se registra en el log del operador: una numeración que da un brinco sin
explicación parece un fallo de la aplicación, cuando lo que hay es un
`config.json` desfasado.
→ `grabador.py`, `iniciar_asalto()` / `_carpeta_ocupada()`; `app.py`, `_iniciar()`.

### El ID del asalto va al FINAL del nombre, tras `_ID_`
`12_47_ID_027`, no `027_12_47`. Los tiradores se identifican por número, y con
el ID delante el operador no podía distinguir de un vistazo cuál de los tres
números era el asalto.

El marcador literal `_ID_` no es decorativo: sin él, un nombre formado solo por
campos numéricos separados por `_` es **ambiguo de analizar** (en `105_212_027`
no hay forma robusta de saber cuál es el asalto, y los números de tirador pueden
tener tres cifras). Con el marcador, `_RE_ASALTO` es exacto.
→ `grabador.py`, `_RE_ASALTO` / `iniciar_asalto()`.

### La lista de asaltos se ordena por ID, no por nombre
Con el ID al final, el orden alfabético agrupa por número de tirador y deja de
ser cronológico. En directo el asalto que se busca es casi siempre el último, así
que se ordena por el ID extraído del nombre. Las carpetas sin ID reconocible van
al final en vez de romper la ordenación.
→ `app.py`, `_clave_orden()` / `_asaltos_en_disco()`.
