"""Genera el mosaico de las DOS LATERALES sobre asaltos YA grabados.

Herramienta suelta, para usar desde la consola. La aplicacion ya genera este
mosaico sola al terminar cada asalto (ver app.py, '_generar_mosaico'); esto
sirve para los asaltos grabados ANTES de que existiera, o para rehacer uno
concreto sin volver a grabar.

Es de solo anadir: escribe el '_M2.mkv' que falte y no toca los POV ni el
mosaico de tres. Por defecto SALTA los asaltos que ya lo tengan, asi que se
puede lanzar sobre una jornada entera las veces que haga falta.

USO

    python mosaico_laterales.py <carpeta> [...]        una o varias carpetas
    python mosaico_laterales.py grabaciones\\MIERCOLES_22   la jornada entera
    python mosaico_laterales.py <carpeta> --rehacer    regenera aunque ya exista
    python mosaico_laterales.py <carpeta> --secuencial uno detras de otro

Acepta tanto la carpeta de UN asalto (la que contiene los .mkv) como la de una
jornada (la que contiene carpetas de asalto): en el segundo caso recorre todas
las que haya dentro.

EN DIRECTO, DURANTE UNA COMPETICION

    Por defecto lanza TODOS los mosaicos a la vez, cada uno en su ventana. Sobre
    una jornada entera son muchos FFmpeg compitiendo por la CPU con las camaras
    que estan grabando. Si la app esta grabando, usar '--secuencial': espera a
    que cada mosaico termine antes de empezar el siguiente, asi solo hay uno
    consumiendo CPU en cada momento.

    Este script NO toca la aplicacion ni sus ficheros de configuracion: solo lee
    los .mkv ya cerrados y escribe uno nuevo. Se puede ejecutar con la app
    abierta y grabando.
"""

import subprocess
import sys
from pathlib import Path

import mosaico

# fps del mosaico. Se lee de config.json si esta; si no, 30, que es el valor por
# defecto del proyecto. Debe casar con la cadencia de grabacion (ver
# DECISIONES.md, 'fps=<fps> explicito al final del filtro').
FPS_POR_DEFECTO = 30

# Que ficheros son las laterales. El orden es el de config.json y el de la
# estructura en disco: cam1 = izquierda, cam2 = frontal/central, cam3 = derecha.
# La central NO entra en este mosaico: es justamente la que se descarta.
_SUFIJOS_LATERALES = ("A", "C")     # con IDs de tirador: 12_47_A.mkv / 12_47_C.mkv
_CLASICOS_LATERALES = ("cam1.mkv", "cam3.mkv")


def _fps() -> int:
    """fps de config.json, o FPS_POR_DEFECTO si no se puede leer.

    No se aborta si falta el fichero o el campo: un fps por defecto razonable es
    preferible a no generar el mosaico. Solo importa que case con la grabacion.
    """
    import json
    cfg = Path(__file__).parent / "config.json"
    try:
        return int(json.loads(cfg.read_text(encoding="utf-8"))["video"]["fps"])
    except (OSError, ValueError, KeyError, TypeError):
        return FPS_POR_DEFECTO


def laterales_de(carpeta: Path) -> tuple[str, str, str] | None:
    """Nombres (izquierda, derecha, salida) del asalto, o None si no es uno.

    Cubre las dos convenciones de nombrado del proyecto (ver CONVENCIONES.md):
    con IDs de tirador ('12_47_A.mkv' / '12_47_C.mkv' -> '12_47_M2.mkv') y sin
    ellos ('cam1.mkv' / 'cam3.mkv' -> 'mosaico2.mkv').

    Devuelve None si en la carpeta no estan las DOS laterales: sin las dos no
    hay mosaico que hacer, y asi una carpeta de jornada -o una a medio grabar-
    se salta sola sin tratarla como error.

    Tambien devuelve None si falta 'metadata.json': ese fichero solo lo escribe
    la app al CERRAR el asalto, asi que su ausencia significa que el asalto se
    esta grabando ahora mismo. Sus .mkv estan creciendo bajo los pies de FFmpeg
    y componerlos daria un mosaico truncado, sin la parte final -y encima
    robando CPU a las camaras en pista-. Es el mismo criterio que usa
    'grabador._carpeta_ocupada()' para saber si una carpeta tiene material de
    verdad. Sin esto hay que vigilar a mano cada asalto en curso.
    """
    if not (carpeta / "metadata.json").exists():
        return None

    # Convencion con IDs: el prefijo sale del propio fichero de la lateral
    # izquierda, quitandole el '_A'. Se busca por sufijo para no depender de
    # como se llamen los tiradores.
    for f in sorted(carpeta.glob("*_A.mkv")):
        prefijo = f.name[:-len("_A.mkv")]
        der = carpeta / f"{prefijo}_C.mkv"
        if der.exists():
            return f.name, der.name, f"{prefijo}_M2.mkv"

    izq, der = (carpeta / _CLASICOS_LATERALES[0]), (carpeta / _CLASICOS_LATERALES[1])
    if izq.exists() and der.exists():
        return izq.name, der.name, "mosaico2.mkv"
    return None


def carpetas_de_asalto(ruta: Path) -> list[Path]:
    """Carpetas de asalto bajo 'ruta', que puede ser un asalto o una jornada.

    Si la propia 'ruta' tiene laterales, es un asalto y se devuelve sola. Si no,
    se mira un nivel hacia dentro (jornada). No se baja mas: la estructura del
    proyecto es 'grabaciones/JORNADA/ASALTO/' y recorrer mas hondo solo podria
    coger cosas que no son asaltos.
    """
    if laterales_de(ruta):
        return [ruta]
    return [d for d in sorted(ruta.iterdir())
            if d.is_dir() and laterales_de(d)]


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    rehacer = "--rehacer" in argv
    secuencial = "--secuencial" in argv

    if not args:
        print(__doc__)
        return 2

    fps = _fps()
    pendientes: list[tuple[Path, tuple[str, str, str]]] = []

    for arg in args:
        ruta = Path(arg).resolve()
        if not ruta.is_dir():
            print(f"  ! no es una carpeta: {ruta}")
            continue
        encontradas = carpetas_de_asalto(ruta)
        if not encontradas:
            print(f"  ! sin asaltos con las dos laterales: {ruta}")
            continue
        for carpeta in encontradas:
            datos = laterales_de(carpeta)
            if datos is None:               # no deberia pasar; defensivo
                continue
            if (carpeta / datos[2]).exists() and not rehacer:
                print(f"  - {carpeta.name}: ya tiene {datos[2]} (--rehacer para forzar)")
                continue
            pendientes.append((carpeta, datos))

    if not pendientes:
        print("\nNada que hacer.")
        return 0

    print(f"\n{len(pendientes)} mosaico(s) de laterales a {fps} fps"
          + (" - uno detras de otro" if secuencial else " - todos a la vez"))

    lanzados = 0
    for carpeta, (izq, der, salida) in pendientes:
        # Segunda comprobacion, JUSTO antes de lanzar. La lista se calculo al
        # empezar y en modo secuencial pueden pasar minutos hasta llegar aqui:
        # en directo, para entonces la app puede haber cerrado un asalto nuevo y
        # estar componiendo sus propios mosaicos. Un '.parcial' en la carpeta
        # significa exactamente eso, y lanzar encima seria pelearse por la CPU
        # con un trabajo que ya esta hecho.
        if list(carpeta.glob("*.parcial.mkv")):
            print(f"  - {carpeta.name}: la app esta generando sus mosaicos, se salta")
            continue

        proc = mosaico.generar_dos(carpeta, izquierda=izq, derecha=der,
                                   fps=fps, salida=salida)
        if proc is None:
            # generar_dos solo devuelve None si falta una entrada o no esta
            # FFmpeg en el PATH; ambas cosas merecen verse en pantalla.
            print(f"  ! {carpeta.name}: no se pudo lanzar ({izq} / {der})")
            continue
        lanzados += 1
        print(f"  > {carpeta.name}: {izq} + {der} -> {salida}")
        if secuencial:
            # Esperar aqui es lo que mantiene un solo FFmpeg en marcha. El
            # codigo de salida del .bat es fiable (ver DECISIONES.md), asi que
            # un fallo real se distingue de un mosaico correcto.
            if proc.wait() != 0:
                print(f"    ! {carpeta.name}: FFmpeg fallo, {salida} NO se ha creado")

    if not secuencial and lanzados:
        print("\nCada mosaico corre en su ventana y se cierra sola al terminar.")
        print("Sobreviven aunque cierres esta consola.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
