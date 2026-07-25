# Flujo de trabajo

## Ejecutar

```
python app.py
```

Sin dependencias externas de Python (solo stdlib). Requiere `ffmpeg`, `ffprobe`,
`ffplay` y `rclone` en el PATH.

## Flujo en directo (operador)

1. **Preparar cámaras.** Cada cámara tiene un desplegable de vídeo, uno de audio
   (micro) y uno de formato. El operador elige la capturadora de cada una; el
   micro se autoempareja (si no, se avisa para elegirlo a mano). El botón
   **Ver** abre una previsualización `ffplay` para comprobar encuadre.
2. **Grabar un asalto.** Se escriben (opcionalmente) los tiradores y se pulsa
   **INICIAR ASALTO** (o Enter). El mismo botón pasa a **DETENER ASALTO**.
3. **Detener.** Al parar, se cierran los tres FFmpeg limpiamente, se escribe
   `metadata.json` y, si las tres cámaras grabaron bien, se lanza el **mosaico**
   en una ventana aparte. Si alguna cámara falló, salta un aviso modal.
4. **Repetir** para cada asalto. La numeración es continua toda la competición.
5. **Subir** al final del día: **Subir todo a Dropbox**.

## Preparación de dispositivos (detalle)

- `_refrescar_dispositivos()` enumera vídeo y audio en una sola llamada a FFmpeg
  y rellena los desplegables. La opción de modo prueba va siempre la primera.
- Se **conserva la elección guardada** aunque el dispositivo no esté conectado
  ahora: se marca "(no disponible)" pero no se borra. Así un cable suelto al
  refrescar no pierde la configuración.
- Elegir un vídeo autoempareja su micro (`asignar_video`). El micro puede
  cambiarse a mano después (`guardar_audio`). El formato se fija por cámara
  (`guardar_formato`): MJPEG (default), YUYV o Auto.
- El formato y el audio solo aplican con capturadora real; en modo prueba sus
  combos quedan deshabilitados.
- **Lo ya asignado no se ofrece al resto.** Un dispositivo (vídeo o micro) que
  ya tiene otra cámara desaparece de los desplegables de las demás: quedan solo
  los libres, que es lo que el operador busca, y no se puede duplicar por
  descuido. Cada cámara sigue viendo su propia elección. No se filtran el modo
  prueba ni "sin audio": no son dispositivos y pueden repetirse.
- Tras cada asignación se repintan los combos con `_repintar_combos()`, que usa
  los mapas ya en memoria. No se reenumera con FFmpeg: eso bloquea la interfaz
  un instante y los dispositivos conectados no han cambiado. Reenumerar es cosa
  de **↻ Refrescar lista** (`_refrescar_dispositivos`).

## Si una cámara se cae a mitad de asalto

En la fila de esa cámara aparece un botón ámbar **⟳ Relanzar**. Al pulsarlo se
vuelve a lanzar sin cortar el asalto; las otras dos no se enteran.

Si la capturadora cambia o vuelve a enumerarse, pulsa **↻ Refrescar lista** y
la fila caída queda editable para escoger el nuevo dispositivo, micro o formato
antes de relanzar. Las demás cámaras siguen bloqueadas.

- Lo grabado antes de la caída **no se pierde**: va en un fichero aparte que se
  une automáticamente al detener el asalto. Queda un solo `camN.mkv`.
- **Se pierde el tramo** entre la caída y el momento de pulsar: se ve en negro.
  Pero el vídeo **sigue sincronizado** con las otras cámaras — ese tramo se
  rellena con un segmento negro real, así que lo que viene después está en su
  sitio y el fichero dura lo mismo que los de las otras cámaras.
- Al terminar sale un aviso indicando qué cámaras se relanzaron, y queda en
  `metadata.json` (campo `intentos`) y en el log de la sesión.

Antes de pulsar, comprobar el cable: si la capturadora sigue desconectada, el
relanzamiento falla y se avisa. Si falla repetidamente, es mejor detener el
asalto y revisar el hardware.

### Caso `Error opening input files: I/O error`

Es un fallo **al arrancar**, no una caída a mitad: esa cámara no llegó a grabar
nada. El botón **⟳ Relanzar** aparece igual y se puede pulsar — pero solo
funcionará si antes se arregla la causa. Suele ser un modo inexistente (el fps de
`config.json` no lo da la capturadora en ese formato) más que un cable suelto;
ver el apartado del `I/O error` en [ERRORES_CONOCIDOS.md](ERRORES_CONOCIDOS.md).

Qué hacer: cambiar esa cámara a **MJPEG** en su desplegable de formato y pulsar
Relanzar. Si vuelve a fallar, el aviso lo dirá y `intentos` seguirá en 0 (no se
cuentan los arranques que no grabaron nada).

## Ciclo de un asalto (motor)

`Sesion.iniciar_asalto(etiqueta)`:
1. Calcula el número (`siguiente_numero()`), lo avanza si ya estuviera grabado, y
   crea la carpeta `grabaciones/JORNADA/ETIQUETA_ID_NNN/`.
2. **Guarda el contador en config.json inmediatamente** (al iniciar, no al
   terminar): si la app muere, el número queda reservado.
3. Crea un `GrabadorCamara` por cámara y los arranca **secuencialmente** (~1 s de
   desfase). Cada uno lanza FFmpeg + un hilo lector de progreso.

`Sesion.detener_asalto()`:
1. **Dos pasadas**: primero `pedir_parada()` en las tres cámaras (envía la `q`,
   que es lo que fija el instante de corte), y solo después `esperar_cierre()`
   en las tres. Así los tres MKV se cierran con duraciones a menos de un
   segundo; camara a camara salían dispares.
2. Compone y escribe `metadata.json` (incluye por cámara: fichero, frames,
   tamaño en bytes, error). Un `tamano_bytes` de 0 delata una cámara que no
   grabó aunque no informara error.
3. Devuelve la metadata (más la ruta de carpeta) para que la GUI avise de fallos
   y lance el mosaico.

## Numeración de asaltos

- La numeración es **continua** durante toda la competición; **no** reinicia por
  jornada.
- El contador vive **solo** en `config.json` (`ultimo_asalto`).
  `siguiente_numero()` devuelve `ultimo_asalto + 1` y no mira el disco:
  renombrar o mover carpetas no lo altera.
- **Para reiniciar o corregir la numeración**, edita `ultimo_asalto` en
  `config.json` con la aplicación cerrada. El siguiente asalto será ese número
  + 1. Es el único sitio que hay que tocar.
- **Protección contra sobrescritura:** si el número calculado ya está grabado en
  esa jornada (pasa al restaurar un `config.json` antiguo), `iniciar_asalto()`
  avanza al primer número libre en vez de grabar encima, y lo registra en el log.
  Una carpeta solo cuenta como ocupada si tiene `metadata.json`. Ver
  [DECISIONES.md](DECISIONES.md).

## Modo prueba

Con todas las `camaras[].dispositivo` a `null`, se graba `testsrc2` en vez de
capturadoras. Permite ejercitar el flujo completo (asaltos, metadata, mosaico,
subida) sin hardware. La GUI muestra un aviso permanente **MODO PRUEBA** en
ámbar: grabar un asalto real creyendo tener capturadoras y acabar con barras de
color sería una pérdida irrecuperable.

## Registro de sesión

El registro **ya no se muestra en la interfaz** (el recuadro se quitó porque
quedaba fuera de la vista). Cada evento (`_escribir()`) se guarda en un fichero de
texto: `logs/FECHA_HORA.txt`, **uno nuevo por cada arranque de la app**
(`_abrir_log()`). Cada línea lleva la hora delante; el fichero abre con una
cabecera fechada. Para consultarlo se abre ese `.txt` (el botón "Abrir carpeta
local" lleva a la carpeta del proyecto).

Es un extra tolerante a fallos: si no se puede crear el fichero (permisos, disco),
la app sigue sin registro en disco, nunca cae por esto (`_escribir()` traga el
`OSError`). Ver `_abrir_log()` / `_escribir()` en `app.py`. La carpeta `logs/`
está en `.gitignore` (material de trabajo).

## Subida a Dropbox

- **Pensada para el final del día.** `_subir()` recorre `grabaciones/*/*` (todos
  los asaltos de todas las jornadas) y lanza `rclone copy` en un hilo.
- Avisa si hay carpetas **sin `metadata.json`** (asalto interrumpido o creado a
  mano) pero deja decidir al operador: ese material puede ser valioso.
- Durante la subida se **bloquea grabar** (y viceversa): rclone no debe leer un
  fichero que se está escribiendo. `--min-age 30s` es la red extra.
- El progreso (bytes, ficheros, velocidad, ETA, asalto en curso) se lee del log
  JSON de rclone y se pinta en la barra y la lista.

### Subida selectiva

Además de **"Subir todo a Dropbox"**, la lista de asaltos permite elegir cuáles
subir: se marcan con **Ctrl/Shift+clic** (`selectmode="extended"`) y se pulsa
**"Subir seleccionados"** (deshabilitado mientras no haya selección). El resto
del flujo —confirmación, bloqueo de botones, progreso— es común a ambos botones
(`_lanzar_subida`).

Implementación: la posición en el `Listbox` se mapea a la carpeta con
`self._asaltos_lista` (rehecho en `_pintar_lista`). `subida.subir()` recibe el
parámetro `carpetas`: si es `None` sube todo (sin filtros); si trae carpetas,
añade un `--include JORNADA/ASALTO/**` por cada una. Se mantiene un **único**
`rclone copy` desde la raíz (una sola barra de progreso, estructura preservada),
nunca `sync`.

### Marca de "ya subido" (tick)

Al terminar una subida **con éxito**, la app escribe un fichero vacío `.subido`
dentro de cada carpeta subida (`_marcar_subidas`). En la lista, esos asaltos
aparecen con un **`✓` verde** al inicio (`_pintar_lista` + `itemconfig`). Sirve
para no resubir por error y ver de un vistazo qué queda pendiente.

- Es una marca **local** ("lo subí desde esta app"), no una consulta a Dropbox.
- Solo se marca si la subida terminó OK; una subida fallida a medias no marca nada.
- El `.subido` se **excluye** de la subida (`--exclude .subido` en `subida.py`):
  es estado local, no debe viajar a Dropbox.
- Vive dentro de la carpeta del asalto (que está en `grabaciones/`, ignorada por
  git). No se mezcla con `metadata.json`.

## Cierre de la aplicación

`_al_cerrar()` intercepta el cierre:
- Si hay un **asalto grabando**, pide confirmación y, si se acepta, lo detiene
  bien (MKV cerrados con metadata, no a medias).
- Si hay una **subida en curso**, pide confirmación (la subida es un hilo daemon;
  no bloquea el cierre).
- Cierra las previsualizaciones `ffplay` abiertas.
- Un **mosaico en marcha NO bloquea el cierre**: es un proceso independiente que
  termina por su cuenta en su ventana. Ver [ARQUITECTURA.md](ARQUITECTURA.md).

## Verificar una grabación

No hay tests automatizados: la verificación se hace grabando y comprobando con
`ffprobe`. Ver el detalle en [ERRORES_CONOCIDOS.md](ERRORES_CONOCIDOS.md).

```
ffprobe -v error -show_entries format=duration -of csv=p=0 grabaciones/.../cam1.mkv
```

- Duración `N/A` → el contenedor se cerró mal (regresión del cierre con `q`, o
  del mosaico).
- Duración muy superior a la real → falta `-re` en modo prueba.
- Frames muy dispares entre cámaras → posible pérdida por ancho de banda USB.
