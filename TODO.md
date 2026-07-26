# Pendiente

Cosas detectadas y no resueltas. Cada una con lo que se sabe, lo que se probó y
por dónde seguir.

## 1. Mosaico que se cuelga (sin causa confirmada)

**Estado:** aparcado. Ocurre de forma intermitente y no se ha logrado reproducir
de forma fiable.

**Síntoma:** el mosaico no termina. La ventana muestra un `speed` muy bajo
(visto `0.415x`), queda un `mosaico.parcial.mkv` **que no crece** (clavado en
1.310.720 bytes) y un `ffmpeg.exe` con más de 1,2 GB de RAM. Visto en
`050_blau_2` y `056_reloj_2_caida_nueva`, ambos asaltos **con relanzamiento**.

**Qué se probó y qué se descartó:**

- *Duraciones dispares entre entradas* → se le añadió `eof_action=pass` a los
  tres overlays. Eso arregló el caso de `050_blau_2` (los mismos ficheros que
  llevaban media hora colgados generaron el mosaico en 10 s).
- *Mapeo de audio* → **descartado**. Con `-map 0:a?` también termina.
- *Cadencia dispar entre entradas* (cam1 a 12,6 fps efectivos frente a 28,9 y
  27,4 de las otras) → se probó normalizar `fps` por entrada antes de escalar,
  en vez de solo al final del filtro. **Descartado como causa:** medido sobre los
  ficheros de `056`, las dos variantes terminan y dan el mismo resultado exacto
  (94,500 s, 2835 frames). fps al final: 11,6 s. fps por entrada: 12,6 s. O sea,
  la versión "arreglada" es incluso un poco más lenta.

**Estado del código:** `mosaico.py` tiene el `fps` normalizado por entrada. No
hace daño, pero **no está confirmado que arregle nada**. Si se quiere volver a la
versión anterior, es quitar el `fps={fps},` del principio de `_celda()`.

**Por dónde seguir:** falta probar el caso real completo — el `.bat` tal cual lo
genera la app, con audio y `preset veryfast`, sobre ficheros de un asalto con
relanzamiento. Todas las pruebas que terminaron bien se lanzaron a mano; el
cuelgue solo se ha visto con el `.bat`. Puede que la diferencia esté ahí (consola
propia, `cmd /c`, buffer de la ventana) y no en el filtro.

**Mientras tanto, si aparece uno colgado:** matar el `ffmpeg.exe`, borrar
`mosaico.parcial.mkv` y `_mosaico.bat`, y relanzarlo. Las grabaciones `camN.mkv`
nunca se ven afectadas: el mosaico es un proceso aparte que solo lee.

## 2. Nunca se llega a 30 fps efectivos (MJPEG descartado como solución)

**Estado:** abierto. La causa que se creía confirmada **no lo está**, y la
solución que se daba por buena **está descartada por medición**.

Ninguna cámara alcanza los 30 fps nominales. Estado actual en `config.json`:
`cam1` y `cam3` en `yuyv422`, `cam2` en `mjpeg`.

| asalto | formato | duración | frames | fps efectivo |
|---|---|---|---|---|
| 044 | yuyv422 ×3 | — | — | 28,7 / 26,5 / 25,0 |
| 056 | yuyv422 ×3 | — | — | 12,6 (relanzada) / 28,9 / 27,4 |
| 024 | yuv / mjpeg / yuv | 34,5 s | 761 / 871 / 882 | 22,1 / 25,2 / 25,6 |
| **016** | **mjpeg ×3** | **31,9 s** | **760 / 794 / 777** | **23,8 / 24,9 / 24,4** |

**Qué se probó y qué se descartó:**

- *Saturación del bus USB por vídeo sin comprimir* → se propuso pasar a MJPEG.
  **Descartado como solución:** en `016`, con las **tres** cámaras en MJPEG, el
  fps efectivo sigue en 23,8-24,9. Comprimir no acercó nada a 30; de hecho el
  mejor registro de toda la tabla sigue siendo `yuyv422` (28,9 en `056`).

**Por dónde seguir:** el techo de ~25 fps con las tres en MJPEG apunta a que el
cuello de botella no es el ancho de banda del bus. Hipótesis sin comprobar:

- Las cámaras de `config.json` son **Iriun Webcam** (dispositivos virtuales, el
  móvil como webcam), no las capturadoras USB. Si las mediciones se tomaron con
  Iriun, el límite puede venir del transporte de Iriun y no decir nada del
  hardware real. **Primer paso: repetir la medición con las capturadoras
  físicas conectadas**, que es el escenario de competición.
- Comprobar qué framerates anuncia de verdad cada dispositivo:
  `ffmpeg -f dshow -list_options true -i video="<nombre>"`.

Sigue siendo la explicación más plausible de los avisos `real-time buffer ...
too full` (ya filtrados como ruido) y quizá del `I/O error` al arrancar alguna
cámara. Ver [docs/ERRORES_CONOCIDOS.md](docs/ERRORES_CONOCIDOS.md).

## 3. Caída real de una capturadora, sin probar

Todo lo de relanzar y unir trozos está verificado con `testsrc2` y parando FFmpeg
por software. **Falta probar el caso real**: desenchufar una capturadora a mitad
de asalto y reconectarla. Ahí dshow puede comportarse distinto a como lo hace un
proceso que se para solo.

**Caso observado sin explicar:** en `026_TEST`, `cam3` terminó con `frames: 0` en
`metadata.json` pese a los 10,1 s de duración del asalto. Si no fue algo
provocado a propósito en esa prueba, es justo el fallo que la prioridad nº 1 (no
perder una grabación) tiene que hacer visible al instante: comprobar si la
interfaz lo señaló mientras grababa o si pasó desapercibido.

## 4. Los laterales del mosaico desaprovechan un tercio de su celda

**Estado:** no es un bug, es una decisión estética pendiente.

No se recorta nada: `force_original_aspect_ratio=decrease` + `pad` garantizan que
la imagen entra entera y sin deformar. Pero las celdas de abajo son 960x360
(relación 2,67:1) y la fuente es 16:9, así que:

| celda | tamaño real de la imagen | negro | aprovechado |
|---|---|---|---|
| frontal 1280x720 | 1280x720 | ninguno | 100 % |
| laterales 960x360 | **640x360** | 160 px a cada lado | **67 %** |

Los laterales quedan a un tercio del tamaño original y con barras negras a los
lados. Opciones si molesta:

- **Recortar a propósito**: `force_original_aspect_ratio=increase` + `crop`.
  Llena la celda pero pierde los bordes superior e inferior de la imagen.
- **Rediseñar la rejilla**: p. ej. laterales de 640x360 centrados con más
  separación, o una disposición en la que las tres celdas sean 16:9.

Decisión del operador: depende de si en los laterales interesa más ver la acción
completa (como ahora) o que se vea grande.

## 5. Hallazgos de la revisión de código, sin abordar

De la revisión inicial, por orden de importancia:

- **Colisión de carpeta de jornada.** `carpeta_dia()` usa día de la semana + día
  del mes (`SABADO_25`), que se repite cada 4 semanas: dos competiciones caerían
  en la misma carpeta. Arreglo propuesto: incluir año y mes
  (`2026-07-25_SABADO`), aceptando ambos formatos en `_RE_JORNADA` durante la
  transición.
- **Las jornadas se ordenan por nombre del día, no por fecha.** `_clave_orden()`
  ordena por `carpeta.parent.name` como texto. Dentro de una jornada el orden es
  correcto (por el ID del asalto), pero **entre** jornadas sale alfabético por
  nombre del día: en una competición de fin de semana, `DOMINGO_26` aparece
  antes que `SABADO_25`. El mismo arreglo del punto anterior lo resuelve de
  paso, porque un prefijo `AAAA-MM-DD` ya ordena cronológicamente como texto.
  (Nota: el día va con `{day:02d}`, así que `MIERCOLES_09` frente a
  `MIERCOLES_22` **no** es un problema — se descartó al verificarlo.)
- **Nombres reservados de Windows.** `_limpiar()` deja pasar `CON`, `PRN`, `AUX`.
  Si el operador los teclea como tiradores, `mkdir` falla y el asalto no arranca
  (se avisa, pero cuesta un asalto). Caso remoto.
- ~~**`_pintar_lista()` hace I/O cada 500 ms.**~~ **Resuelto**: `_tamano_mb()`
  cachea por carpeta cerrada (las que ya tienen `metadata.json`). Medido: 0,75 ms
  por refresco. La carpeta del asalto en curso se recalcula a propósito, porque
  su tamaño sube mientras graba.
