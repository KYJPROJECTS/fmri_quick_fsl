#!/usr/bin/env python3
import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Bootstrap para poder importar common/ estando en una subcarpeta del repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.config import load_config

try:
    import nibabel as nib
except ImportError:
    nib = None  # La validación de npts se salta con un aviso si falta nibabel.

CFG = load_config()

SCRIPT_DIR = Path(__file__).resolve().parent
MOTION_THRESHOLD_MM = CFG.motion_threshold_mm

TASK_INCLUDE_PATTERN = re.compile(r"bold", re.IGNORECASE)
TASK_EXCLUDE_PATTERN = re.compile(r"field_mapping|fieldmap|t1_se_tra|t1w", re.IGNORECASE)

VISOVERBAL_PATTERN = re.compile(r"viso.?verbal", re.IGNORECASE)
FSF_VISOVERBAL = "150x30.fsf"
FSF_DEFAULT = "100x20.fsf"


# --------------------------------------------------------------------------
# Estructura de datos: reemplaza los dicts sueltos {"task_label": ..., ...}
# --------------------------------------------------------------------------

@dataclass
class FeatTask:
    task_label: str
    proc: subprocess.Popen
    feat_dir: Path
    start_time: float = field(default_factory=time.time)
    done: bool = False


# --------------------------------------------------------------------------
# Utilidades de consola
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Paso 1: estructura de carpetas del paciente
# --------------------------------------------------------------------------

def create_patient_structure(base_dir: Path, patient_name: str, subj_id: str) -> dict:
    patient_dir = base_dir / patient_name
    dicom_dir = patient_dir / "dicom" / f"sub-{subj_id}"
    quick_output_dir = patient_dir / "quick_output"
    nifti_dir = quick_output_dir / "nifti"

    dicom_dir.mkdir(parents=True, exist_ok=True)
    nifti_dir.mkdir(parents=True, exist_ok=True)

    banner("Estructura de paciente creada")
    print(f"Paciente:        {patient_name}")
    print(f"Carpeta base:    {patient_dir}")
    print("Carpetas creadas:")
    print(f"  - {dicom_dir}")
    print(f"  - {quick_output_dir}")
    print(f"  - {nifti_dir}")
    print(f"\nLista para copiar las DICOM en:\n  {dicom_dir}\n")

    return {
        "patient_dir": patient_dir,
        "dicom_dir": dicom_dir,
        "quick_output_dir": quick_output_dir,
        "nifti_dir": nifti_dir,
    }


# --------------------------------------------------------------------------
# Paso 2: esperar a que lleguen las DICOM
# --------------------------------------------------------------------------

def wait_for_dicom(dicom_dir: Path, poll_interval: int = 5, stable_checks: int = 3) -> int:
    banner("Esperando llegada de archivos DICOM...")
    print(f"Monitoreando: {dicom_dir}")
    print("(Ctrl+C para cancelar la espera)\n")

    last_count = -1
    stable_count = 0

    try:
        while True:
            current_count = len(list(dicom_dir.glob("*.dcm")))
            if current_count != last_count:
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] Archivos DICOM detectados: {current_count}")
                last_count = current_count
                stable_count = 0
            else:
                stable_count += 1

            if current_count > 0 and stable_count >= stable_checks:
                print(f"\nRecepción estable: {current_count} archivos DICOM, sin cambios en "
                      f"los últimos {stable_checks * poll_interval}s.")
                return current_count

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        sys.exit("\n[CANCELADO] Espera de DICOM interrumpida por el usuario.")


# --------------------------------------------------------------------------
# Paso 3: conversión DICOM -> NIfTI y detección de tareas
# --------------------------------------------------------------------------

def convert_dicom_to_nifti(dicom_dir: Path, nifti_out_dir: Path) -> None:
    banner("Convirtiendo DICOM -> NIfTI")
    nifti_out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "dcm2niix",
        "-f", "%d_%t",
        "-p", "n",
        "-z", "y",
        "-b", "y",
        "-ba", "y",
        "-o", str(nifti_out_dir),
        str(dicom_dir),
    ]
    print(f"Comando: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        sys.exit(f"[ERROR] dcm2niix falló:\n{result.stderr}")


def find_task_niftis(nifti_dir: Path) -> list[Path]:
    all_niftis = sorted(nifti_dir.glob("*.nii.gz"))
    task_niftis = [
        p for p in all_niftis
        if TASK_INCLUDE_PATTERN.search(p.name) and not TASK_EXCLUDE_PATTERN.search(p.name)
    ]
    if not task_niftis:
        sys.exit(f"[ERROR] No se detectaron NIfTI de tareas funcionales en {nifti_dir}. "
                  f"Archivos encontrados: {[p.name for p in all_niftis]}")
    return task_niftis


def suggest_template(nifti_path: Path) -> str:
    if VISOVERBAL_PATTERN.search(nifti_path.name):
        return FSF_VISOVERBAL
    return FSF_DEFAULT


# --------------------------------------------------------------------------
# Paso 4: revisión/edición interactiva de la asignación tarea -> template
# --------------------------------------------------------------------------

def review_template_assignments(task_niftis: list[Path], templates_dir: Path) -> dict:
    available_templates = sorted(p.name for p in templates_dir.glob("*.fsf"))
    if not available_templates:
        sys.exit(f"[ERROR] No se encontraron archivos .fsf en {templates_dir}")

    assignments = {p: suggest_template(p) for p in task_niftis}

    while True:
        banner("Asignación de templates .fsf propuesta")
        for i, (nifti_path, template) in enumerate(assignments.items(), start=1):
            print(f"  [{i}] {nifti_path.name}  ->  {template}")

        if ask_yes_no("\n¿Está de acuerdo con esta asignación?"):
            break

        print(f"\nTemplates disponibles en {templates_dir}: {available_templates}")
        idx_str = input("¿Qué número de tarea desea cambiar? (o 'listo' para terminar): ").strip().lower()
        if idx_str == "listo":
            continue
        try:
            idx = int(idx_str)
            nifti_path = list(assignments.keys())[idx - 1]
        except (ValueError, IndexError):
            print("Número inválido, intente de nuevo.")
            continue

        new_template = input(f"Nuevo template para '{nifti_path.name}' {available_templates}: ").strip()
        if new_template not in available_templates:
            print(f"'{new_template}' no está en {templates_dir}, no se hizo el cambio.")
            continue
        assignments[nifti_path] = new_template

    return assignments


# --------------------------------------------------------------------------
# Paso 5: construir .fsf y lanzar FEAT (sin bloquear al lanzarlo)
# --------------------------------------------------------------------------

def get_real_volume_count(nifti_path: Path) -> int | None:
    """Número real de volúmenes (4ta dimensión) del NIfTI. Devuelve None si
    nibabel no está instalado o el archivo no se puede leer, en vez de
    fallar el pipeline por esto."""
    if nib is None:
        return None
    try:
        img = nib.load(str(nifti_path))
        shape = img.shape
        return shape[3] if len(shape) >= 4 else 1
    except Exception as e:
        print(f"[AVISO] No se pudo leer {nifti_path.name} con nibabel: {e}")
        return None


def validate_npts(template_text: str, nifti_path: Path) -> None:
    """Compara fmri(npts) del template contra el número real de volúmenes
    del NIfTI. El nombre del archivo (ej. '150x30.fsf') NO garantiza que el
    valor interno sea correcto, así que esto se verifica de verdad en vez
    de confiar en el nombre."""
    match = re.search(r'set fmri\(npts\)\s+(\d+)', template_text)
    if not match:
        print(f"[AVISO] No se encontró 'fmri(npts)' en el template. No se "
              f"pudo validar contra {nifti_path.name}.")
        return

    npts_template = int(match.group(1))
    npts_real = get_real_volume_count(nifti_path)

    if npts_real is None:
        print(f"[AVISO] No se pudo determinar el número real de volúmenes "
              f"de {nifti_path.name} (¿nibabel instalado?). Template dice "
              f"npts={npts_template}, sin verificar.")
        return

    if npts_template != npts_real:
        print(f"[ALERTA] Desajuste de volúmenes para {nifti_path.name}: "
              f"el template dice fmri(npts)={npts_template}, pero el NIfTI "
              f"real tiene {npts_real} volúmenes.")
        if not ask_yes_no("¿Continuar de todas formas con este template?"):
            sys.exit(f"[DETENIDO] Corrige el template o verifica "
                      f"{nifti_path.name} antes de continuar.")
    else:
        print(f"[OK] npts del template ({npts_template}) coincide con "
              f"{nifti_path.name} ({npts_real} volúmenes).")


def build_fsf(template_path: Path, input_4d: Path, output_feat_base: Path, fsf_work_dir: Path) -> Path:
    """Copia el template y sustituye SOLO fmri(outputdir) y feat_files(1),
    validando antes que fmri(npts) coincida con el NIfTI real."""
    text = template_path.read_text(encoding="utf-8")

    validate_npts(text, input_4d)

    text, n_out = re.subn(
        r'set fmri\(outputdir\)\s+".*?"',
        f'set fmri(outputdir) "{output_feat_base}"',
        text,
    )
    text, n_in = re.subn(
        r'set feat_files\(1\)\s+".*?"',
        f'set feat_files(1) "{input_4d}"',
        text,
    )

    if n_out == 0 or n_in == 0:
        sys.exit(
            f"[ERROR] No se encontraron las claves 'fmri(outputdir)' y/o "
            f"'feat_files(1)' en {template_path}. Revisar sintaxis real del "
            f"archivo y ajustar build_fsf()."
        )

    fsf_work_dir.mkdir(parents=True, exist_ok=True)
    fsf_out = fsf_work_dir / f"design_{output_feat_base.name}_{datetime.now().strftime('%H%M%S')}.fsf"
    fsf_out.write_text(text, encoding="utf-8")
    return fsf_out


def launch_feat(nifti_path: Path, template_path: Path, quick_output_dir: Path) -> FeatTask:
    """Construye el .fsf y lanza FEAT SIN esperar a que termine."""
    task_label = nifti_path.name.replace(".nii.gz", "")
    feat_base = quick_output_dir / task_label
    feat_dir_final = quick_output_dir / f"{task_label}.feat"

    fsf_path = build_fsf(template_path, nifti_path, feat_base, quick_output_dir / "_fsf_generated")

    print(f"  Lanzando FEAT: {task_label}  (template={template_path.name})")
    proc = subprocess.Popen(
        ["feat", str(fsf_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    return FeatTask(task_label=task_label, proc=proc, feat_dir=feat_dir_final)


def wait_for_all_feat(tasks: list[FeatTask], poll_interval: int = 5, timeout_sec: int = 1800) -> None:
    """Hace polling de las tareas dadas hasta que cada una termine."""
    deadline = time.time() + timeout_sec

    while any(not t.done for t in tasks):
        if time.time() > deadline:
            pending = [t.task_label for t in tasks if not t.done]
            sys.exit(f"[ERROR] Timeout esperando FEAT. Tareas incompletas: {pending}")

        for t in tasks:
            if t.done:
                continue
            report_marker = t.feat_dir / "report.html"
            stats_marker = t.feat_dir / "thresh_zstat1.nii.gz"
            if report_marker.exists() and stats_marker.exists():
                mtime_before = report_marker.stat().st_mtime
                time.sleep(5)
                if report_marker.exists() and report_marker.stat().st_mtime == mtime_before:
                    t.done = True
                    elapsed = int(time.time() - t.start_time)
                    print(f"  [OK] {t.task_label} completado en ~{elapsed}s")
                    print_console_qc(t.feat_dir, t.task_label)
                    open_report(report_marker)

        time.sleep(poll_interval)


def open_report(report_html: Path) -> None:
    """Abre el reporte de FEAT en el navegador de Windows vía wslview.
    No bloquea el pipeline si wslview no está disponible."""
    try:
        subprocess.Popen(["wslview", str(report_html)])
    except FileNotFoundError:
        print(f"[AVISO] 'wslview' no está disponible. Abre manualmente:\n"
              f"  {report_html}")


# --------------------------------------------------------------------------
# QC en consola
# --------------------------------------------------------------------------

def print_console_qc(feat_dir: Path, task_label: str) -> None:
    abs_rms = feat_dir / "mc" / "prefiltered_func_data_mcf_abs.rms"
    if abs_rms.exists():
        values = [float(v) for v in abs_rms.read_text().split()]
        max_disp = max(values)
        status = "OK" if max_disp <= MOTION_THRESHOLD_MM else "REVISAR"
        print(f"       Movimiento máximo: {max_disp:.3f} mm (umbral {MOTION_THRESHOLD_MM} mm) -> {status}")
    else:
        print("       Movimiento: no disponible")

    log_path = feat_dir / "report_log.html"
    if log_path.exists():
        content = log_path.read_text(encoding="utf-8", errors="ignore")
        has_error = bool(re.search(r"error", content, re.IGNORECASE))
        print(f"       Errores en log FSL: {'SI - revisar' if has_error else 'no detectados'}")
    else:
        print("       Log de errores: no disponible")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="FSL feat rápido fMRI por paciente (verificar activaciones)")
    parser.add_argument("--patient", required=True, help='Nombre de carpeta del paciente, ej. "ArangoValenciaKarenNicolle"')
    parser.add_argument("--base-dir", default=str(CFG.funcionales_dir), help="Carpeta 'funcionales' donde viven los pacientes")
    parser.add_argument("--subj", default=CFG.subject_id, help=f"ID de sujeto BIDS (default {CFG.subject_id})")
    parser.add_argument("--templates-dir", default=str(CFG.templates_dir), help="Carpeta con los .fsf")
    parser.add_argument("--poll-interval", type=int, default=5, help="Segundos entre chequeos de DICOM/FEAT")
    parser.add_argument("--stable-checks", type=int, default=2, help="Chequeos consecutivos sin cambio para dar por completa la recepción DICOM")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    templates_dir = Path(args.templates_dir).resolve()

    paths = create_patient_structure(base_dir, args.patient, args.subj)

    dicom_count = wait_for_dicom(paths["dicom_dir"], args.poll_interval, args.stable_checks)

    if not ask_yes_no(f"\nSe recibieron {dicom_count} archivos DICOM. ¿Iniciar procesamiento ahora?"):
        print("Procesamiento cancelado por el usuario. Puede volver a correr el script cuando esté listo.")
        return

    convert_dicom_to_nifti(paths["dicom_dir"], paths["nifti_dir"])
    task_niftis = find_task_niftis(paths["nifti_dir"])

    assignments = review_template_assignments(task_niftis, templates_dir)

    banner(f"Procesando {len(assignments)} tarea(s) de forma secuencial...")
    for nifti_path, template_name in assignments.items():
        t = launch_feat(nifti_path, templates_dir / template_name, paths["quick_output_dir"])
        wait_for_all_feat([t], args.poll_interval)


if __name__ == "__main__":
    main()
