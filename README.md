# ESGRIMA_26 — Grabación multicámara de asaltos

Soporte de grabación para una competición de esgrima: tres cámaras sobre la pista,
un fichero por POV y asalto, y subida a OneDrive al terminar el evento.

## Uso

```
python app.py
```

1. Escribir los tiradores (opcional) y pulsar **INICIAR ASALTO**.
2. Pulsar **DETENER ASALTO** al acabar.
3. Repetir. La numeración es automática y continúa aunque se reinicie la aplicación.
4. Al terminar la jornada, **Subir todo a OneDrive**.

Durante la grabación, cada cámara muestra un indicador:

| Color | Significado |
|-------|-------------|
| Verde | Grabando correctamente |
| Ámbar | Sin señal — imagen congelada (revisar cable HDMI) |
| Rojo  | Cámara caída |

En el recuadro de subida se listan los asaltos grabados con su tamaño, y se
marca con `>` el que se está transfiriendo. La barra y el detalle (MB, ficheros,
velocidad, tiempo restante) se actualizan cada segundo.

## Estructura resultante

Se replica tal cual en `personal:ESGRIMA_26`:

```
grabaciones/
  MIERCOLES_22/                 carpeta de la jornada
    001_Garcia_Lopez/           ID_NOMBRE1_NOMBRE2
      cam1.mkv                  Lateral izquierda
      cam2.mkv                  Frontal
      cam3.mkv                  Lateral derecha
      metadata.json             tiempos, duración e incidencias
    002_Munoz_Perez/
  JUEVES_23/
    003_Ruiz_Sanz/
```

La numeración es **continua durante toda la competición**, no se reinicia cada
jornada: así el asalto 47 es único y basta su número para identificarlo.

El contador vive en `config.json` (`ultimo_asalto`) y se guarda al **iniciar**
cada asalto, de modo que un cierre inesperado no reutiliza un número. Renombrar
carpetas ya no lo altera. Como respaldo, se contrasta con los asaltos que haya
en disco: si `config.json` se pierde o se restaura una copia antigua, la
numeración no retrocede sobre material ya grabado.

## Hardware

- 3 × capturadora HDMI → USB 3.0 (ALBURAN, 1080p60)
- Repartir las tres en **puertos de controladores distintos**. El equipo tiene
  cuatro controladores USB 3.10 independientes, así que hay margen de sobra.

## Configuración

`config.json`:

- `video.resolucion` / `video.fps` — 1080p30 por defecto. Suficiente para revisión
  técnica; subir a 60 fps triplica el tamaño y rara vez aporta.
- `video.preset` — `medium` por defecto: mejor compresión (ficheros más pequeños)
  a igual calidad. El equipo (Ryzen 7, 16 hilos) codifica las tres cámaras a la
  vez con margen de sobra. Si algún equipo más flojo se quedara corto de CPU en
  directo, `fast` o `veryfast` alivian a costa de ficheros algo mayores.
- `video.crf` — 22 es calidad alta (menor número = más calidad y más tamaño).
  Independiente del preset: el crf fija cómo se ve, el preset cuánto ocupa.
- `camaras[].dispositivo` — `null` activa el modo prueba (patrón `testsrc2`,
  sin necesidad de hardware). Se elige desde la interfaz con el desplegable de
  cada cámara; la elección se guarda aquí y persiste entre sesiones.
- `rclone_destino` — `personal:ESGRIMA_26`.

En el recuadro **Cámaras**, cada fila tiene un desplegable para elegir su
dispositivo (o dejarla en modo prueba) y un botón **Ver** que abre una ventana
con la imagen en vivo (útil para ajustar el encuadre). El botón **↻ Refrescar
lista** vuelve a consultar lo conectado. Al asignar, **comprobar que cada cámara
apunta a la posición correcta en la pista**: confundir el POV frontal con un
lateral estropea el material de toda la jornada. Un dispositivo elegido que luego
se desconecta se marca como *(no disponible)* pero no se pierde la configuración.

La previsualización usa `ffplay` en una ventana aparte de 640×360 (16:9), para
no tapar la aplicación. No se puede previsualizar y grabar la misma capturadora a
la vez (DirectShow lo impide), así que al iniciar un asalto las previews se
cierran solas y el botón se desactiva mientras se graba.

### Audio

Cada cámara HDMI trae su propio micro. Al elegir una capturadora, su micro se
**autoempareja** por el nombre entre paréntesis (`USB Video #2` →
`Microphone (USB Video #2)`), lo que funciona incluso con varias capturadoras
idénticas. El segundo desplegable (**micro**) de cada cámara permite cambiarlo a
mano o ponerlo en *sin audio*. El audio se graba en el mismo MKV, en AAC. Durante
la grabación, las cámaras con audio muestran un ♪.

## Decisiones de diseño

- **MKV, no MP4.** Si se corta la luz o el programa muere, un MP4 queda corrupto e
  irrecuperable; un MKV se reproduce hasta donde llegó.
- **Un proceso FFmpeg por cámara.** Si una capturadora se desconecta, las otras dos
  siguen grabando.
- **Parada con `q`, no matando el proceso.** Es lo que permite que el fichero quede
  con su duración escrita.
- **Subida con `rclone copy`, nunca `sync`.** `sync` borraría en destino.
- **`--min-age 30s` en la subida.** rclone falla al copiar un fichero que está
  creciendo. El botón ya se bloquea mientras se graba, pero esto cubre además
  una segunda instancia abierta o una subida lanzada desde la consola.

## Documentación técnica

Este README es la guía del operador. La documentación para desarrollar sobre el
proyecto está en [docs/](docs/):

- [Arquitectura](docs/ARQUITECTURA.md) · [Convenciones](docs/CONVENCIONES.md) ·
  [Decisiones e invariantes](docs/DECISIONES.md)
- [Glosario](docs/GLOSARIO.md) · [Flujo de trabajo](docs/FLUJO_DE_TRABAJO.md) ·
  [Errores conocidos](docs/ERRORES_CONOCIDOS.md)

## Pendiente

- Previsualización simultánea de los tres POVs empotrada en la interfaz
  (la actual abre una ventana `ffplay` por cámara; lo simultáneo requeriría
  MediaMTX).

## Notas originales

Vamos a dar soporte a una competición de esgrima.
Vamos a montar una pista con cámaras (mínimo 3) para grabar distintos asaltos.

La idea es iniciar a grabar cuando se inicie un asalto y parar la grabación cuando termine.

Mandar a través de RCLONE a una carpeta en OneDrive. En esa carpeta, tener carpetas
individuales por asalto para tener los tres POVs.
En RCLONE es el sitio de "personal:" y la carpeta está en la raíz "ESGRIMA_26"
