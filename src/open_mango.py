#!/usr/bin/env python3
import argparse
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_FUNCIONALES_DIR = "/mnt/c/Users/karen/Desktop/funcionales"
DEFAULT_MANGO_EXE = "/mnt/c/Program Files/Mango/Mango.exe"

# Patrón para capturar el rango "min ... max" que aparece junto a la etiqueta
THRESHOLD_RANGE_PATTERN = re.compile(
    r"Thresholded activation images.{0,200}?([\d.]+).{0,100}?([\d.]+)",
    re.IGNORECASE | re.DOTALL,
)

# Patrones de respaldo (solo el mínimo) por si el de arriba no coincide
THRESHOLD_PATTERNS = [
    re.compile(r"Z\s*>\s*([\d.]+)", re.IGNORECASE),
    re.compile(r"threshold[^0-9]*([\d.]+)", re.IGNORECASE),
]


def banner(msg: str) -> None:
    print(f"\n{'=' * 70}\n{msg}\n{'=' * 70}")


def ask_yes_no(prompt: str) -> bool:
    while True:
        resp = input(f"{prompt} [s/n]: ").strip().lower()
        if resp in ("s", "si", "sí", "y", "yes"):
            return True
        if resp in ("n", "no"):
            return False
        print("Responde 's' o 'n'.")


def find_completed_feat_dirs(quick_output_dir: Path) -> list[Path]:
    feat_dirs = sorted(quick_output_dir.glob("*.feat"))
    completed = [d for d in feat_dirs if (d / "thresh_zstat1.nii.gz").exists() and (d / "example_func.nii.gz").exists()]
    if not completed:
        sys.exit(f"[ERROR] No se encontraron carpetas .feat completas (con thresh_zstat1.nii.gz) en {quick_output_dir}")
    return completed


def extract_z_threshold(feat_dir: Path) -> str | None:
    """
    Devuelve un string listo para mostrar, ej. "2.3 a 8.4", extraído de la
    línea "Thresholded activation images" de report_poststats.html (el mismo
    rango que se ve junto a la barra de colores en el reporte de FSL).
    Si no se encuentra ese patrón exacto, cae a buscar solo el mínimo
    ("Z > X") como respaldo.
    """
    poststats = feat_dir / "report_poststats.html"
    if not poststats.exists():
        return None
    text = poststats.read_text(encoding="utf-8", errors="ignore")
    plain_text = re.sub(r"<[^>]+>", " ", text)
    plain_text = re.sub(r"\s+", " ", plain_text)

    match = THRESHOLD_RANGE_PATTERN.search(plain_text)
    if match:
        return f"{match.group(1)} a {match.group(2)}"

    for pattern in THRESHOLD_PATTERNS:
        match = pattern.search(plain_text)
        if match:
            return f"{match.group(1)} (solo mínimo, no se encontró el máximo)"

    return None


def to_windows_path(p: Path) -> str:
    """Convierte una ruta de WSL (/mnt/c/...) a una ruta de Windows (C:\\...)
    para que el .exe de Windows pueda encontrar el archivo."""
    result = subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def open_in_mango(example_func: Path, thresh_zstat: Path, mango_exe: str) -> None:
    cmd = [mango_exe, to_windows_path(example_func), "-o", to_windows_path(thresh_zstat)]
    print(f"  Comando: {' '.join(cmd)}")
    try:
        subprocess.Popen(cmd)
    except FileNotFoundError:
        sys.exit(
            f"[ERROR] No se encontró el ejecutable de Mango en '{mango_exe}'. "
            f"Verificar la ruta real con --mango-exe."
        )


def main():
    parser = argparse.ArgumentParser(description="Abre en Mango el functional + máscara de activación de cada tarea de un paciente")
    parser.add_argument("--patient", required=True, help='Nombre de carpeta del paciente, ej. "ArangoValenciaKarenNicolle"')
    parser.add_argument("--base-dir", default=DEFAULT_FUNCIONALES_DIR, help="Carpeta 'funcionales' donde viven los pacientes")
    parser.add_argument("--subj", default="01", help="ID de sujeto (no usado directamente, solo informativo)")
    parser.add_argument("--mango-exe", default=DEFAULT_MANGO_EXE, help=f"Ruta al ejecutable de Mango en Windows (default: {DEFAULT_MANGO_EXE})")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    quick_output_dir = base_dir / args.patient / "quick_output"

    if not quick_output_dir.exists():
        sys.exit(f"[ERROR] No existe {quick_output_dir}. ¿Corriste run_patient.py para este paciente primero?")

    feat_dirs = find_completed_feat_dirs(quick_output_dir)

    banner(f"Tareas completas encontradas para {args.patient}")
    for i, d in enumerate(feat_dirs, start=1):
        print(f"  [{i}] {d.name}")

    for i, feat_dir in enumerate(feat_dirs, start=1):
        example_func = feat_dir / "example_func.nii.gz"
        thresh_zstat = feat_dir / "thresh_zstat1.nii.gz"
        z_threshold = extract_z_threshold(feat_dir)

        banner(f"Abriendo en Mango: {feat_dir.name}")
        open_in_mango(example_func, thresh_zstat, args.mango_exe)

        if z_threshold:
            print(f"  Umbral Z según el reporte: {z_threshold} (Thresholded activation images)")
        else:
            print("  [WARN] No se pudo extraer el umbral Z de report_poststats.html. "
                  "Revísalo manualmente en el reporte de esa tarea.")

        is_last = (i == len(feat_dirs))
        if not is_last:
            input("\nAjusta el umbral en Mango y cierra la ventana cuando termines. "
                  "Presiona Enter aquí para continuar con la siguiente tarea...")


if __name__ == "__main__":
    main()