# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Idioma

El código, los comentarios, los nombres de identificadores y la interfaz están en
español. Mantener esa convención. Los identificadores evitan tildes y eñes
(`tamano_bytes`, `SIN_SENAL`) para no depender de la codificación del terminal.

## Ejecutar

```
python app.py
```

Sin dependencias externas de Python: solo stdlib (Tkinter incluido). Sí requiere
`ffmpeg`, `ffprobe` y `rclone` en el PATH.

No hay tests automatizados. La verificación se hace ejecutando una grabación real
y comprobando los ficheros resultantes con `ffprobe` (ver más abajo).

## Modo prueba

Cuando `config.json` tiene todos los `camaras[].dispositivo` a `null`, la aplicación
graba con el patrón sintético `testsrc2` en lugar de capturadoras. Esto permite
desarrollar el flujo completo (asaltos, metadata, subida) sin hardware conectado.
El botón "Detectar capturadoras" reescribe `config.json` asignando dispositivos
reales; poner los valores a `null` de nuevo vuelve al modo prueba.

## Arquitectura

Cuatro módulos, con la GUI desacoplada del motor:

- `grabador.py` — motor. `Sesion` coordina el conjunto; `GrabadorCamara` envuelve
  un proceso FFmpeg y publica su estado en `EstadoCamara`. No sabe nada de Tkinter.
- `app.py` — GUI Tkinter. Hace polling de `EstadoCamara` cada 500 ms para pintar
  los semáforos; no recibe callbacks del motor.
- `dispositivos.py` — enumeración DirectShow vía FFmpeg, y filtro de webcams
  virtuales (Iriun, OBS Virtual…) que no son capturadoras reales.
- `subida.py` — `rclone copy` en un hilo, con callbacks de progreso.

**Un proceso FFmpeg independiente por cámara**, no uno con varias entradas. Si una
capturadora se desconecta, las demás siguen grabando. Cada `GrabadorCamara` lanza
además un hilo que lee el stderr de FFmpeg (`-progress pipe:2`) para contar frames
y detectar errores.

Los callbacks que vienen de hilos (progreso de subida) entran en Tkinter mediante
`self.after(0, ...)`; no tocar widgets directamente desde un hilo.

## Invariantes que no deben romperse

Estas decisiones tienen motivo y revertirlas causa fallos difíciles de ver:

- **Contenedor MKV, no MP4.** Un corte de corriente deja un MP4 corrupto e
  irrecuperable; el MKV se reproduce hasta donde llegó.
- **Detener FFmpeg escribiendo `q` en stdin**, no con `terminate()`/`kill()`. Solo
  el cierre limpio escribe la duración en el contenedor. Por eso el proceso se lanza
  con `stdin=PIPE` y **sin** `-nostdin`. `terminate()` queda como plan B por timeout.
- **`rclone copy`, nunca `sync`.** `sync` borraría en OneDrive.
- **`-re` en las entradas `lavfi`.** Sin él, `testsrc2` genera frames a velocidad de
  CPU y las duraciones grabadas no se corresponden con el tiempo real.
- **Filtrado de ruido de FFmpeg** (`_RUIDO` en `grabador.py`). Avisos benignos como
  los de Fontconfig marcarían cámaras como caídas en mitad de la competición. Al
  añadir avisos nuevos a esa lista, comprobar que no enmascaran fallos reales.
- **`_detencion_pedida`** distingue una parada nuestra de una caída real: FFmpeg
  sale con código distinto de cero de forma legítima cuando lo paramos nosotros.

## Numeración de asaltos y estructura de carpetas

`grabaciones/MIERCOLES_22/007_Garcia_Lopez/` — jornada, luego `ID_NOMBRE1_NOMBRE2`.
La numeración es continua durante toda la competición, no reinicia por jornada.

El contador está en `config.json` (`ultimo_asalto`) y se escribe al **iniciar** el
asalto, no al terminarlo: si la aplicación muere a mitad, el número queda
reservado y no se reutiliza.

`_maximo_en_disco()` es solo una red de seguridad por si `config.json` se pierde.
Exige tres condiciones para contar una carpeta, y las tres importan:
carpeta de jornada válida (`_RE_JORNADA`), nombre `NNN` de tres dígitos
(`_RE_ASALTO`), y **presencia de `metadata.json`**. Sin la última, una carpeta
creada a mano como `500_revisar` dispararía el contador a 501.

Al cambiar el formato de los nombres, revisar también el `glob("*/*")` de
`_subir()` en `app.py`: recorre la estructura anidada y es fácil olvidarlo.

## Verificar cambios en la grabación

Tras tocar la construcción de comandos FFmpeg, grabar un asalto y comprobar que la
duración del fichero coincide con la del asalto y que no hay errores:

```
ffprobe -v error -show_entries format=duration -of csv=p=0 grabaciones/asalto_001*/cam1.mkv
```

Una duración `N/A` significa que el contenedor se cerró mal (regresión del cierre
con `q`). Una duración muy superior a la real indica que falta `-re` en modo prueba.

## Contexto de despliegue

Se usa en directo durante una competición, sobre Windows y con un operador que
puede no ser el desarrollador. Prioridades, en orden: no perder una grabación,
hacer visible cualquier fallo al instante, y evitar falsas alarmas. Un aviso
espurio de "cámara caída" durante un asalto es un fallo grave, no un detalle.

`mediamtx.exe` y `mediamtx.yml` están en el repositorio sin usar todavía: quedan
reservados para la previsualización en vivo de los tres POVs, que aún no está
implementada.
