# Glosario

Términos del dominio (esgrima y grabación) y del código.

## Dominio

| Término | Significado |
|---|---|
| **Asalto** | Un combate de esgrima. Unidad de grabación: cada asalto es una carpeta con los tres vídeos y su metadata. |
| **Jornada** | Un día de competición. Carpeta `DIA_NN` que agrupa los asaltos de ese día. La numeración de asaltos **no** reinicia por jornada. |
| **Tiradores** | Los dos esgrimistas del asalto. Su nombre (opcional) va en el nombre de la carpeta. |
| **POV** | *Point of view*, cada uno de los tres ángulos de cámara (lateral izquierda, frontal, lateral derecha). |
| **Mosaico** | Vídeo 1920x1080 que compone los tres POV: frontal grande arriba, los dos laterales abajo. |

## Hardware

| Término | Significado |
|---|---|
| **Capturadora** | Cajita HDMI→USB que convierte la señal de una cámara en un dispositivo de vídeo USB que el PC ve vía DirectShow. Aquí suelen aparecer como "USB3.0 Video". |
| **Fuente** | La videocámara que genera la señal HDMI (p.ej. una Panasonic AVCCAM). Distinta de la capturadora. |
| **DirectShow (dshow)** | API de Windows para dispositivos de captura. FFmpeg accede a las capturadoras con `-f dshow`. |
| **MJPEG** | Formato comprimido dentro de la capturadora (`-vcodec mjpeg`). Ocupa poco: varias cámaras caben en el bus USB. |
| **YUYV / yuyv422** | Vídeo sin comprimir (`-pixel_format yuyv422`). Mucho ancho de banda; a 1080p una sola cámara puede rondar 1.5 Gbps. |
| **testsrc2** | Patrón sintético de FFmpeg (`lavfi`) que se graba en modo prueba cuando no hay capturadoras. |

## Código

| Símbolo | Módulo | Qué es |
|---|---|---|
| `Sesion` | grabador | Coordina las tres cámaras y numera los asaltos. Lo que maneja la GUI. |
| `Camara` | grabador | Definición estática de una cámara (viene de config.json). Fuente de verdad al persistir. |
| `GrabadorCamara` | grabador | Envuelve **un** proceso FFmpeg y vigila su salida. |
| `EstadoCamara` | grabador | Estado vivo de una grabación; lo escribe el hilo lector, lo lee la GUI por sondeo. |
| `EstadoCamara.bloqueada` | grabador | Propiedad que detecta imagen congelada (frames sin avanzar > 5 s). |
| `_detencion_pedida` | grabador | Bandera: distingue una parada nuestra de una caída real de FFmpeg. |
| `_RUIDO` | grabador | Lista de avisos benignos de FFmpeg que no deben marcarse como fallo. |
| `Dispositivo` | dispositivos | Un dispositivo DirectShow: `nombre` visible + `id` de hardware (único). |
| `emparejar_audio` | dispositivos | Asocia el micro de una capturadora a su vídeo por el nombre. |
| `_raiz` | dispositivos | Núcleo comparable de un nombre de dispositivo, para emparejar. |
| `Progreso` | subida | Instantánea del avance de la subida (bytes, ficheros, ETA, asalto en curso). |
| `App` | app | La ventana Tkinter y todos sus manejadores. |
| `_refrescar` | app | Bucle de 500 ms que repinta los indicadores. Único sitio donde se pintan. |
| `filas` | app | Lista de tuplas de widgets por cámara; se empareja con `camaras`/`grabadores` por `zip()`. |

## Convenciones de nombres

- Identificadores **sin tildes ni eñes** (`tamano_bytes`, `SIN_SENAL`). Ver
  [CONVENCIONES.md](CONVENCIONES.md).
- Prefijo `_` = privado del módulo/clase (`_entrada`, `_maximo_en_disco`,
  `_RE_JORNADA`).
- Constantes en mayúsculas (`VERDE`, `MODO_PRUEBA`, `_ANCHO`, `_ALTO_SUP`).
