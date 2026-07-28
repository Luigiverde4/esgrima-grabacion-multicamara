# Convenciones

## Idioma

El código, los comentarios, los nombres de identificadores y la interfaz están
en **español**. Mantener esa convención.

Los identificadores **evitan tildes y eñes** (`tamano_bytes`, `SIN_SENAL`,
`_segmento_negro`) para no depender de la codificación del terminal. En cambio,
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
- **Asalto**: `TIRADOR1_TIRADOR2_ID_NNN` — `12_47_ID_027`. El ID va al **final**,
  tras el marcador literal `_ID_`, para no confundirse con los números de
  tirador. Solo `ID_NNN` si no se indicaron tiradores. Tres dígitos
  obligatorios. Regex: `_RE_ASALTO`. Ver [DECISIONES.md](DECISIONES.md).
- **Ficheros de cámara**: `ID1_ID2_A.mkv`, `_B.mkv`, `_C.mkv` — los dos números
  de tirador y una letra por cámara (**A**=cam1, **B**=cam2, **C**=cam3, mapa
  `GrabadorCamara._LETRAS`). Ejemplo: `12_47_A.mkv`.
  **Respaldo:** si el operador no escribe exactamente dos números, se cae a
  `cam1.mkv`, `cam2.mkv`, `cam3.mkv` (el `id` de cada `Camara`). La app avisa
  antes de empezar, pero **deja grabar**: no se pierde un asalto por un campo a
  medio rellenar.
- **Mosaico de tres POVs**: `ID1_ID2_M.mkv` (`M` fuera de la serie A/B/C para
  distinguirlo de un POV), o `mosaico.mkv` sin IDs.
- **Mosaico de las dos laterales**: `ID1_ID2_M2.mkv`, o `mosaico2.mkv` sin IDs.
  Se genera **además** del anterior, no en su lugar (ver
  [DECISIONES.md](DECISIONES.md)).

  En ambos, el `.parcial` se deriva del nombre final (`12_47_M.parcial.mkv`,
  `12_47_M2.parcial.mkv`) mientras se genera. Que sean distintos es lo que
  permite que los dos mosaicos corran a la vez sin pisarse.
- **Trozos de un relanzamiento**: sufijo en minúscula sobre el nombre base —
  `12_47_A_b.mkv`, `12_47_A_c.mkv`. Temporales: desaparecen al unir.
- **Metadata**: `metadata.json`. Incluye `prefijo` (`"12_47"` o `""`) para saber
  ya en la nube por qué un asalto lleva un esquema de nombres u otro.

Todos los nombres de cámara derivan de una única propiedad,
`GrabadorCamara.base`, para que un asalto con IDs y uno sin ellos recorran el
mismo código: el fichero final, los trozos, los negros temporales y el unido
salen de ahí. `Sesion.ids_tiradores()` extrae los dos números del texto libre y
devuelve `None` si no hay **exactamente** dos (con uno o con tres, adivinar cuál
es cuál daría nombres engañosos).

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
  "rclone_destino": "remoto:CARPETA_COMPETICION",    // remoto:carpeta de rclone
  "competicion": "Valencia_Fencing_2026",
  "ultimo_asalto": 26,                     // contador continuo; UNICA fuente de la numeracion
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

## Tamaños en la lista de asaltos

Cada asalto muestra lo que ocupa **toda su carpeta** (vídeos, mosaico y
metadata), no solo los `.mkv`: lo que importa al mirar si cabe la jornada es el
espacio real en disco. Bajo la lista hay un resumen con el número de asaltos, el
total (en GB a partir de 1000 MB) y cuántos quedan sin subir.

El tamaño entra en la firma de caché de `_pintar_lista()`. Sin él, el asalto en
curso se pintaba una vez con 0 MB —la carpeta ya existe pero el `.mkv` acaba de
crearse— y no se repintaba nunca más, porque el resto de la firma no cambiaba.
→ `app.py`, `_tamano_mb()` / `_pintar_lista()` / `_actualizar_total()`.

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
