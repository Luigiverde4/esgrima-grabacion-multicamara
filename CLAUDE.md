# CLAUDE.md

Guía para Claude Code (claude.ai/code) al trabajar en este repositorio.

Aplicación de escritorio (Windows) para grabar asaltos de esgrima con tres
cámaras, componer un mosaico de los tres POVs y subirlo todo a la nube.

## Documentación

La documentación de referencia está repartida en seis documentos. **Consúltalos
antes de tocar la parte que cubren** — contienen el porqué de decisiones no
obvias que, revertidas, causan fallos difíciles de ver:

- [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) — módulos, un proceso FFmpeg por
  cámara, cómo se comunican motor y GUI, el mosaico como proceso aparte,
  estructura en disco.
- [docs/CONVENCIONES.md](docs/CONVENCIONES.md) — idioma, estilo, nombres de
  carpetas, formato de `config.json`, colores de estado.
- [docs/DECISIONES.md](docs/DECISIONES.md) — **invariantes que no deben
  romperse**, con qué/por qué/dónde. Leer antes de cambiar comandos FFmpeg,
  cierre de procesos, subida o numeración.
- [docs/GLOSARIO.md](docs/GLOSARIO.md) — términos de esgrima, hardware y código.
- [docs/FLUJO_DE_TRABAJO.md](docs/FLUJO_DE_TRABAJO.md) — cómo ejecutar, flujo del
  operador, ciclo de un asalto, numeración, modo prueba, subida, cierre.
- [docs/ERRORES_CONOCIDOS.md](docs/ERRORES_CONOCIDOS.md) — bugs reales ya
  diagnosticados (I/O error en YUV, mosaico `N/A`, escapado en cmd, falsas
  alarmas) y cómo verificar con `ffprobe`. **Mira aquí antes de "arreglar" algo
  que parezca un bug.**

Al cambiar el comportamiento del código, **actualiza el documento que lo
describe** (son la fuente de verdad; este CLAUDE.md solo indexa).

## Lo imprescindible

- **Idioma:** todo en español. Identificadores **sin tildes ni eñes**
  (`tamano_bytes`, `SIN_SENAL`). Detalle en [docs/CONVENCIONES.md](docs/CONVENCIONES.md).

- **Ejecutar:** `python app.py`. Solo stdlib de Python; requiere `ffmpeg`,
  `ffprobe`, `ffplay` y `rclone` en el PATH.

- **Sin tests automatizados.** La verificación se hace grabando un asalto real y
  comprobando con `ffprobe` (ver [docs/ERRORES_CONOCIDOS.md](docs/ERRORES_CONOCIDOS.md)):

  ```
  ffprobe -v error -show_entries format=duration -of csv=p=0 grabaciones/.../cam1.mkv
  ```

  Duración `N/A` = contenedor cerrado mal (regresión del cierre con `q` o del
  mosaico).

- **Contexto de despliegue:** se usa **en directo** durante una competición,
  sobre Windows, con un operador que puede no ser el desarrollador. Prioridades,
  en orden: **(1) no perder una grabación, (2) hacer visible cualquier fallo al
  instante, (3) evitar falsas alarmas.** Un aviso espurio de "cámara caída"
  durante un asalto es un fallo grave. Todas las decisiones de
  [docs/DECISIONES.md](docs/DECISIONES.md) se justifican contra estas prioridades.

- **Invariantes críticas** (detalle y motivo en [docs/DECISIONES.md](docs/DECISIONES.md)):
  MKV no MP4; detener FFmpeg con `q` en stdin (no `terminate`/`kill`); `rclone
  copy` nunca `sync`; los objetos `Camara` son la fuente de verdad al persistir
  `config.json`; el contador de asaltos se escribe al **iniciar**; el mosaico es
  un proceso independiente con `.parcial` que se renombra al terminar.

- **Pendiente (no implementado):** previsualización en vivo simultánea de los
  tres POVs empotrada en la interfaz (requeriría MediaMTX; hoy no hay binario en
  el repo). La previsualización actual abre una ventana `ffplay` por cámara.
