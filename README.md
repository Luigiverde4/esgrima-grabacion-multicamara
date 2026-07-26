# ESGRIMA_26

Grabación multicámara de asaltos de esgrima para uso en competición: tres cámaras
sobre la pista, un fichero por POV y asalto, mosaico de los tres ángulos y subida
a la nube al terminar la jornada.

Aplicación de escritorio para Windows, en Python y Tkinter, **sin dependencias de
terceros**: todo lo pesado lo hacen FFmpeg y rclone.

> Se usa **en directo** durante una competición: mientras hay un asalto en marcha
> no hay margen para depurar nada, y una grabación perdida no se repite. De ahí las
> prioridades del proyecto, por orden: (1) no perder una grabación, (2) hacer
> visible cualquier fallo al instante, (3) evitar falsas alarmas.

## Índice

- [Requisitos](#requisitos) · [Puesta en marcha](#puesta-en-marcha) · [Uso](#uso)
- [Estructura de las grabaciones](#estructura-de-las-grabaciones) · [Configuración](#configuración)
- [Decisiones de diseño](#decisiones-de-diseño) · [Documentación](#documentación) · [Estado](#estado)

## Requisitos

**Python 3.11 o superior.** No hay que instalar paquetes: el proyecto usa solo la
biblioteca estándar, Tkinter incluido.

Cuatro ejecutables deben estar accesibles en el `PATH`:

| Ejecutable | Para qué |
|---|---|
| `ffmpeg` | grabar cada cámara y componer el mosaico |
| `ffprobe` | verificar las grabaciones |
| `ffplay` | previsualizar cada cámara en vivo |
| `rclone` | subir la jornada al remoto configurado |

En Windows:

```powershell
winget install Gyan.FFmpeg Rclone.Rclone
```

`rclone` necesita además un remoto configurado (`rclone config`). Sirve cualquiera
de los que soporta rclone —Dropbox, Google Drive, OneDrive, S3, un servidor SFTP—:
el proyecto no depende de ninguno en concreto.

### Hardware

- 3 × capturadora HDMI → USB 3.0 (ALBURAN, 1080p60).
- Repartir las tres en **puertos de controladores USB distintos**. El equipo de
  referencia tiene cuatro controladores USB 3.10 independientes.

## Puesta en marcha

```powershell
git clone https://github.com/Luigiverde4/esgrima-grabacion-multicamara.git
cd esgrima-grabacion-multicamara
copy config.ejemplo.json config.json
python app.py
```

`config.json` **no se versiona**: contiene los identificadores DirectShow de las
capturadoras, válidos solo en el PC donde se enumeraron, y el contador
`ultimo_asalto`. Por eso se parte de `config.ejemplo.json`.

No hace falta configurar nada para arrancar: mientras una cámara no tenga
capturadora asignada, se graba un patrón `testsrc2` en su lugar. Las capturadoras
y sus micros se asignan luego desde los desplegables de la propia interfaz, y la
elección se guarda sola.

## Uso

1. Escribir los dos números de tirador separados por un espacio — `12 47`,
   opcional — y pulsar **INICIAR ASALTO**.
2. Pulsar **DETENER ASALTO** al acabar.
3. Repetir. La numeración es automática y sobrevive a un reinicio de la aplicación.
4. Al terminar la jornada, **Subir todo a la nube**.

Durante la grabación, cada cámara muestra un indicador de estado:

| Color | Significado |
|---|---|
| 🟢 Verde | Grabando correctamente |
| 🟠 Ámbar | Sin señal — imagen congelada (revisar cable HDMI) |
| 🔴 Rojo | Cámara caída |

Si una cámara se cae a mitad de asalto se puede relanzar: sus trozos se unen
rellenando el hueco con negro, de forma que los tres ficheros siguen durando lo
mismo y el mosaico no se descuadra.

En el recuadro de subida se listan los asaltos grabados con su tamaño y se marca
con `>` el que se está transfiriendo, con barra de progreso y detalle (MB,
ficheros, velocidad, tiempo restante) actualizados cada segundo.

### Cámaras y audio

Cada fila del recuadro **Cámaras** tiene un desplegable para elegir su dispositivo
y un botón **Ver** que abre la imagen en vivo en una ventana de 640×360, útil para
ajustar el encuadre. **↻ Refrescar lista** vuelve a consultar lo conectado.

> [!WARNING]
> Al asignar, comprobar que cada cámara apunta a la posición correcta en la pista.
> Confundir el POV frontal con un lateral estropea el material de toda la jornada.

No se puede previsualizar y grabar la misma capturadora a la vez (DirectShow lo
impide), así que al iniciar un asalto las previews se cierran solas.

Cada cámara HDMI trae su propio micro, que se **autoempareja** por el nombre entre
paréntesis (`USB Video #2` → `Microphone (USB Video #2)`), lo que funciona incluso
con varias capturadoras idénticas. El audio se graba en el mismo MKV, en AAC, y
las cámaras con audio muestran un ♪. Los desplegables solo ofrecen lo que está
libre: lo que ya usa otra cámara no aparece en el resto.

## Estructura de las grabaciones

Se replica tal cual en el destino de rclone:

```
grabaciones/
  MIERCOLES_22/                 carpeta de la jornada
    12_47_ID_027/               TIRADOR1_TIRADOR2_ID_NNN (el ID va al final)
      cam1.mkv                  Lateral izquierda
      cam2.mkv                  Frontal
      cam3.mkv                  Lateral derecha
      mosaico.mkv               los tres POV en un 1920x1080
      metadata.json             tiempos, duración e incidencias
    002_Munoz_Perez/
  JUEVES_23/
    003_Ruiz_Sanz/
```

La numeración es **continua durante toda la competición**, no se reinicia cada
jornada: así el asalto 47 es único y basta su número para identificarlo.

El contador vive en `config.json` (`ultimo_asalto`) y se guarda al **iniciar**
cada asalto, de modo que un cierre inesperado no reutiliza un número. Renombrar
carpetas no lo altera. Como respaldo se contrasta con los asaltos que haya en
disco: si `config.json` se pierde o se restaura una copia antigua, la numeración
no retrocede sobre material ya grabado.

## Configuración

| Clave | Qué hace |
|---|---|
| `video.resolucion` / `video.fps` | 1080p30 por defecto. Suficiente para revisión técnica; 60 fps triplica el tamaño y rara vez aporta. |
| `video.preset` | `medium`: mejor compresión a igual calidad. En un equipo justo de CPU, `fast` o `veryfast` alivian a costa de ficheros mayores. |
| `video.crf` | 22, calidad alta. Menor número = más calidad y más tamaño. Independiente del preset: el crf fija cómo se ve, el preset cuánto ocupa. |
| `camaras[].dispositivo` | `null` activa el modo prueba (`testsrc2`, sin hardware). Se elige desde la interfaz y persiste. |
| `rclone_destino` | Formato `remoto:carpeta` de rclone, donde `remoto` es el nombre que le diste en `rclone config`. |

## Decisiones de diseño

- **MKV, no MP4.** Si se corta la luz o el programa muere, un MP4 queda corrupto e
  irrecuperable; un MKV se reproduce hasta donde llegó.
- **Un proceso FFmpeg por cámara.** Si una capturadora se desconecta, las otras dos
  siguen grabando.
- **Parada con `q` por stdin, no matando el proceso.** Es lo que permite que el
  fichero quede con su duración escrita.
- **El mosaico es un proceso independiente**, con su propia consola: su avance se
  ve y un fallo suyo no arrastra a las grabaciones.
- **Subida con `rclone copy`, nunca `sync`.** `sync` borraría en destino.
- **`--min-age 30s` en la subida.** rclone falla al copiar un fichero que está
  creciendo.

El porqué completo de cada una, con sus invariantes, está en
[docs/DECISIONES.md](docs/DECISIONES.md).

## Verificación

No hay tests automatizados: la verificación se hace grabando un asalto real y
comprobándolo con `ffprobe`.

```bash
ffprobe -v error -show_entries format=duration -of csv=p=0 grabaciones/.../cam1.mkv
```

Una duración `N/A` significa que el contenedor se cerró mal. Ver
[docs/ERRORES_CONOCIDOS.md](docs/ERRORES_CONOCIDOS.md) antes de dar nada por roto:
varios síntomas que parecen fallos están documentados como comportamiento esperado.

## Documentación

Este README es la guía del operador. Para desarrollar sobre el proyecto:

| Documento | Contenido |
|---|---|
| [ARQUITECTURA.md](docs/ARQUITECTURA.md) | Módulos, un proceso FFmpeg por cámara, cómo hablan motor y GUI |
| [DECISIONES.md](docs/DECISIONES.md) | Invariantes que no deben romperse, con su porqué |
| [CONVENCIONES.md](docs/CONVENCIONES.md) | Idioma, estilo, nombres de carpetas, formato de `config.json` |
| [FLUJO_DE_TRABAJO.md](docs/FLUJO_DE_TRABAJO.md) | Ciclo de un asalto, numeración, modo prueba, subida |
| [ERRORES_CONOCIDOS.md](docs/ERRORES_CONOCIDOS.md) | Bugs ya diagnosticados y cómo verificarlos |
| [GLOSARIO.md](docs/GLOSARIO.md) | Términos de esgrima, hardware y código |

## Estado

Funcional y probado en pista. El tag
[`probado-en-directo`](../../releases/tag/probado-en-directo) marca el estado
verificado durante las pruebas in situ del 25 de julio de 2026.

Pendientes conocidos en [TODO.md](TODO.md), entre ellos:

- Ninguna cámara alcanza los 30 fps efectivos, y pasar a MJPEG **no** lo resolvió.
- La carpeta de jornada (`SABADO_25`) colisiona cada 4 semanas.
- Previsualización simultánea de los tres POV empotrada en la interfaz: requeriría
  MediaMTX, hoy no implementada.

## Licencia

Sin licencia definida. Todos los derechos reservados por el autor.
