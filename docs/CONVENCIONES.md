# Convenciones

## Idioma

El código, los comentarios, los nombres de identificadores y la interfaz están
en **español**. Mantener esa convención.

Los identificadores **evitan tildes y eñes** (`tamano_bytes`, `SIN_SENAL`,
`_maximo_en_disco`) para no depender de la codificación del terminal. En cambio,
el texto que se muestra al operador y los datos (nombres de tiradores, etiquetas)
sí llevan acentos: los ficheros se leen/escriben con `encoding="utf-8"` y los
JSON con `ensure_ascii=False`.

## Estilo

- Sin dependencias externas de Python: **solo stdlib**. No añadir paquetes de
  terceros. Tkinter viene incluido.
- Docstrings y comentarios explican **el porqué**, no el qué. Muchos comentarios
  del código justifican una decisión no obvia (ver [DECISIONES.md](DECISIONES.md)).
- Los procesos externos (`ffmpeg`, `ffplay`, `rclone`) se lanzan con
  `creationflags=CREATE_NO_WINDOW` para no abrir consolas — **excepto** el
  mosaico, que usa `CREATE_NEW_CONSOLE` a propósito (ventana de progreso).

## Nombres de carpetas y ficheros

- **Jornada**: `DIA_NN` en mayúsculas sin tilde — `MIERCOLES_22`, `SABADO_04`.
  Generado por `Sesion.carpeta_dia()`. Regex de validación: `_RE_JORNADA`.
- **Asalto**: `NNN_NOMBRE1_NOMBRE2` — `007_Garcia_Lopez`. Solo el `NNN` si no se
  indicaron tiradores. Tres dígitos obligatorios. Regex: `_RE_ASALTO`.
- **Ficheros de cámara**: `cam1.mkv`, `cam2.mkv`, `cam3.mkv` (el `id` de cada
  `Camara`).
- **Mosaico**: `mosaico.mkv` (y `mosaico.parcial.mkv` mientras se genera).
- **Metadata**: `metadata.json`.

`Sesion._limpiar()` convierte el texto libre del operador ("Garcia vs Lopez") en
nombre de carpeta válido: conserva letras acentuadas y eñes (`\w` con
`flags=UNICODE`), quita signos problemáticos (`/ \ : * ? " < > |`), pasa
separadores a `_` y corta a 60 caracteres (límite de ruta de Windows).

## config.json

Fuente de configuración y de estado persistente. Campos:

```json
{
  "modo_prueba": false,                    // derivado: true si ninguna cámara configurada
  "carpeta_grabaciones": "grabaciones",
  "rclone_destino": "personal:ESGRIMA_26", // remoto:carpeta de rclone
  "competicion": "ESGRIMA_26",
  "ultimo_asalto": 26,                     // contador continuo (ver FLUJO_DE_TRABAJO.md)
  "video": {
    "resolucion": "1920x1080",
    "fps": 30,
    "preset": "medium",                    // preset de libx264 (compresion; no calidad)
    "crf": 22                              // calidad (menor = mejor)
  },
  "camaras": [ /* una entrada por cámara, en orden izq, frontal, der */ ]
}
```

Cada cámara:

```json
{
  "id": "cam1",
  "nombre": "Lateral izquierda",           // descripción para el operador
  "dispositivo": "@device_pnp_...",        // id DirectShow del vídeo; null => modo prueba
  "dispositivo_nombre": "USB3.0 Video",    // nombre visible, para mostrar
  "audio": null,                           // id DirectShow del micro; null => sin audio
  "formato": "yuyv422"                     // mjpeg | yuyv422 | auto
}
```

### Reglas al tocar config.json

- **Los objetos `Camara` son la fuente de verdad.** `_persistir_camaras()`
  reconstruye el bloque `camaras` desde ellos, no al revés, para que vídeo y
  audio nunca se desincronicen. Ver [DECISIONES.md](DECISIONES.md).
- **Reescritura releyendo antes.** `_actualizar_config()` relee el fichero antes
  de escribir, para no pisar ajustes cambiados a mano mientras la app estaba viva.
- El campo `audio` tiene default `None`: configs anteriores a su introducción
  cargan sin romperse.

## Modo prueba

Cuando **todas** las `camaras[].dispositivo` están a `null`, la app graba con el
patrón sintético `testsrc2` en lugar de capturadoras. Permite desarrollar el
flujo completo (asaltos, metadata, mosaico, subida) sin hardware. Ver
[FLUJO_DE_TRABAJO.md](FLUJO_DE_TRABAJO.md).

## Colores de estado (GUI)

Definidos en `app.py`, pensados para leerse de un vistazo desde lejos:

| Constante | Color | Significado |
|---|---|---|
| `VERDE` | `#1a7f37` | grabando OK / dispositivo asignado / subida correcta |
| `ROJO` | `#c9252d` | error / cámara caída / fallo de subida |
| `AMBAR` | `#bf8700` | modo prueba / imagen congelada (sin señal) |
| `GRIS` | `#57606a` | reposo / sin dispositivo / texto secundario |

Los botones de estado principal usan `tk.Button` (no `ttk.Button`): ttk no deja
fijar color de fondo en Windows, y aquí el verde/rojo es la señal principal.
