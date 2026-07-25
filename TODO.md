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

## 2. Pérdida de frames por USB con `yuyv422`

**Estado:** diagnosticado, con solución conocida, sin aplicar.

Las tres cámaras están en `yuyv422` a 1080p30 en `config.json`. Sin comprimir
satura el bus USB y se pierden frames de verdad:

| asalto | cámara | fps efectivo |
|---|---|---|
| 044 | cam1 / cam2 / cam3 | 28,7 / 26,5 / 25,0 |
| 056 | cam1 (relanzada) | 12,6 |
| 056 | cam2 / cam3 | 28,9 / 27,4 |

Es la causa de fondo de los avisos `real-time buffer ... too full` (ya filtrados
como ruido) y probablemente también del `I/O error` al arrancar alguna cámara.

**Solución:** pasar las cámaras a **MJPEG** en su desplegable de formato, o
conectar las capturadoras a puertos USB 3.0 con ancho de banda suficiente. Es un
cambio desde la interfaz, sin tocar código. Ver
[docs/ERRORES_CONOCIDOS.md](docs/ERRORES_CONOCIDOS.md).

**Pendiente de verificar:** grabar un asalto en MJPEG y comparar `frames` en
`metadata.json` — deberían acercarse a `duración × 30`.

## 3. Caída real de una capturadora, sin probar

Todo lo de relanzar y unir trozos está verificado con `testsrc2` y parando FFmpeg
por software. **Falta probar el caso real**: desenchufar una capturadora a mitad
de asalto y reconectarla. Ahí dshow puede comportarse distinto a como lo hace un
proceso que se para solo.

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
  en la misma carpeta. Además `_asaltos_en_disco()` ordena alfabéticamente, así
  que `MIERCOLES_9` sale después de `MIERCOLES_22`. Arreglo propuesto: incluir
  año y mes (`2026-07-25_SABADO`), aceptando ambos formatos en `_RE_JORNADA`
  durante la transición.
- **Nombres reservados de Windows.** `_limpiar()` deja pasar `CON`, `PRN`, `AUX`.
  Si el operador los teclea como tiradores, `mkdir` falla y el asalto no arranca
  (se avisa, pero cuesta un asalto). Caso remoto.
- ~~**`_pintar_lista()` hace I/O cada 500 ms.**~~ **Resuelto**: `_tamano_mb()`
  cachea por carpeta cerrada (las que ya tienen `metadata.json`). Medido: 0,75 ms
  por refresco. La carpeta del asalto en curso se recalcula a propósito, porque
  su tamaño sube mientras graba.
