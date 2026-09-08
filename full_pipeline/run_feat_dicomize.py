#!/usr/bin/env python3
"""
run_feat_dicomize.py
=====================
Automatiza el bloque final del pipeline fMRI (a partir del paso 9 de la
guía): extracción de confounds, FEAT con EVs de confusión, validación de
resultados y dicomización de activaciones.

Continúa exactamente donde termina run_bids_fmriprep.py.

USO BÁSICO
----------
    python3 run_feat_dicomize.py --patient "ArangoValenciaKarenNicolle"

Corre, para cada tarea funcional detectada en fmriprep_output/:
    1. fmri-confounds-extract         (conda run -n bioimg, nativo en WSL)
    2. FEAT con EVs de confusión      (feat design.fsf, nativo en WSL)
    3. QC de movimiento + errores     (automático)
    4. Checkpoint de validación       (postats: revisión humana obligatoria)
    5. nifti2dicom                    (conda run -n bioimg, nativo en WSL)

REANUDAR / CORRER SOLO UN BLOQUE
----------------------------------
    python3 run_feat_dicomize.py --patient "..." --steps confounds
    python3 run_feat_dicomize.py --patient "..." --steps feat
    python3 run_feat_dicomize.py --patient "..." --steps dicomize
    python3 run_feat_dicomize.py --patient "..." --steps feat,dicomize --tasks lenguajehistoria

REQUISITOS ANTES DE CORRER
----------------------------
1. run_bids_fmriprep.py ya debe haber terminado (bids_output/ y
   fmriprep_output/ deben existir y estar completos).
2. Debes colocar tus plantillas reales de la guía en:
       fmri_full_pipeline/templates/100x20.fsf
       fmri_full_pipeline/templates/150x30.fsf
   Este script NO las genera — no tengo su contenido y no voy a inventarlo.
3. Debe existir un entorno conda (default: "bioimg") con fmri-confounds-extract
   y nifti2dicom instalados, exactamente como en la guía.

SUPUESTOS Y DECISIONES QUE DEBES REVISAR
-------------------------------------------
- El sufijo del archivo de entrada a FEAT se tomó del EJEMPLO REAL en el pie
  de página de la guía (..._desc-preproc_bold.nii.gz, espacio MNI152NLin6Asym),
  no de la prosa de la sección 9 (que dice "...bold.feat.nii.gz", inconsistente
  con su propio ejemplo). Verifica el nombre real de tus archivos antes de
  confiar ciegamente en la detección automática.
- Elección de plantilla FSF: 150x30.fsf si el nombre de la tarea contiene
  "viso" (visoverbal), 100x20.fsf en cualquier otro caso — mismo criterio
  que ya usábamos en run_patient.py para el QC rápido en equipo.
- El archivo de confounds se asume en formato texto plano compatible con el
  diálogo "Select Confound EVs text files" de FEAT (así lo indica la guía al
  usarlo directamente en esa casilla).
- ALERTA DE ESPACIO EN PACIENTES CON LESIÓN: si el paciente tiene lesión,
  fmriprep usa el registro ANTs en espacio MNI152NLin2009cAsym para la
  anatómica pero produce las funcionales en MNI152NLin6Asym (por el flag
  --output-spaces). Este script TE AVISA de esta posible incompatibilidad
  de espacios antes de dicomizar; no la resuelve por ti.
- `--dcmref` (el DICOM de referencia para nifti2dicom) se autodetecta como
  el primer .dcm encontrado en dicom/sub-XX si no lo pasas explícitamente.
  Esto es una adivinanza razonable, no una garantía de que sea el DICOM
  correcto para esa tarea — pásalo explícitamente si tienes dudas.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Bootstrap para poder importar common/ estando en una subcarpeta del repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.config import load_config

CFG = load_config()

# Los .fsf (100x20.fsf, 150x30.fsf) SÍ se comparten con quick_qc/, por eso
# usan CFG.templates_dir en vez de una carpeta local a full_pipeline/.
TEMPLATES_DIR = CFG.templates_dir

VALID_STEPS = ["confounds", "feat", "dicomize"]
MOTION_THRESHOLD_MM = CFG.motion_threshold_mm
ZSTAT_THRESHOLD_DEFAULT = CFG.zstat_vmin
ALPHA_DEFAULT = CFG.alpha


# ---------------------------------------------------------------------------
# Utilidades generales (mismo estilo que run_bids_fmriprep.py)
# ---------------------------------------------------------------------------

def run(cmd, description, check=True, capture=True):
    print(f"\n>>> {description}")
    print(f"    $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if capture:
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
    if check and result.returncode != 0:
        print(f"\n[ERROR] El paso '{description}' terminó con código "
              f"{result.returncode}.", file=sys.stderr)
        sys.exit(result.returncode)
    return result


def confirm(prompt):
    answer = input(f"{prompt} [s/n]: ").strip().lower()
    return answer in ("s", "si", "sí", "y", "yes")


def check_conda_env(env_name):
    result = subprocess.run(["conda", "env", "list"], capture_output=True, text=True)
    if env_name not in result.stdout:
        print(f"[ERROR] No se encontró el entorno conda '{env_name}'. "
              f"Créalo o pasa --confound-env con el nombre correcto.")
        sys.exit(1)
    print(f"[OK] Entorno conda '{env_name}' encontrado.")


def strip_nii_ext(path: Path) -> str:
    """Quita .nii.gz o .nii del final, tal como espera FSL en sus fsf."""
    s = str(path)
    if s.endswith(".nii.gz"):
        return s[: -len(".nii.gz")]
    if s.endswith(".nii"):
        return s[: -len(".nii")]
    return s


# ---------------------------------------------------------------------------
# Detección de tareas funcionales
# ---------------------------------------------------------------------------

def has_lesion(base_dir: Path, subj_id: str) -> bool:
    anat_deriv = base_dir / "anat_derivatives" / f"sub-{subj_id}" / "anat"
    return any(anat_deriv.glob(f"sub-{subj_id}_space-*_desc-preproc_T1w.nii.gz"))


def get_functional_tasks(base_dir: Path, subj_id: str) -> list:
    """Detecta tareas a partir de los archivos de confounds que produce
    fmriprep (uno por tarea, independiente del espacio de salida)."""
    func_dir = base_dir / "fmriprep_output" / f"sub-{subj_id}" / "func"
    if not func_dir.exists():
        print(f"[ERROR] No existe {func_dir}. ¿Ya corriste fmriprep?")
        sys.exit(1)

    pattern = re.compile(rf"sub-{subj_id}_task-([A-Za-z0-9]+)_desc-confounds_timeseries\.tsv")
    tasks = sorted({
        m.group(1)
        for f in func_dir.glob(f"sub-{subj_id}_task-*_desc-confounds_timeseries.tsv")
        if (m := pattern.match(f.name))
    })
    if not tasks:
        print(f"[ERROR] No se encontraron archivos *_desc-confounds_timeseries.tsv "
              f"en {func_dir}.")
        sys.exit(1)
    print(f"[OK] Tareas funcionales detectadas: {tasks}")
    return tasks


def get_preproc_bold_path(base_dir: Path, subj_id: str, task: str) -> Path:
    """Busca el NIfTI preprocesado por fmriprep para una tarea, en espacio
    MNI. Ver nota en el docstring del módulo sobre la discrepancia de
    sufijo entre la prosa de la guía y su propio ejemplo en pie de página."""
    func_dir = base_dir / "fmriprep_output" / f"sub-{subj_id}" / "func"
    candidates = list(func_dir.glob(
        f"sub-{subj_id}_task-{task}_space-*_desc-preproc_bold.nii.gz"
    ))
    if not candidates:
        print(f"[ERROR] No se encontró el bold preprocesado para la tarea "
              f"'{task}' en {func_dir}. Revisa el sufijo real de tus "
              f"archivos: puede diferir de '_desc-preproc_bold.nii.gz'.")
        sys.exit(1)
    if len(candidates) > 1:
        print(f"[AVISO] Más de un archivo candidato para la tarea '{task}', "
              f"usando el primero: {candidates[0].name}")
    return candidates[0]


def get_confound_file(base_dir: Path, task: str) -> Path:
    confounds_dir = base_dir / "derivatives" / "confounds"
    candidates = list(confounds_dir.glob(f"*{task}*"))
    if not candidates:
        print(f"[ERROR] No se encontró archivo de confounds para la tarea "
              f"'{task}' en {confounds_dir}. ¿Corriste --steps confounds?")
        sys.exit(1)
    return candidates[0]


# ---------------------------------------------------------------------------
# Paso 9a: fmri-confounds-extract
# ---------------------------------------------------------------------------

def step_confounds(base_dir: Path, subj_id: str, tasks: list, conda_env: str):
    print("\n" + "=" * 70)
    print("PASO 9a: EXTRACCIÓN DE FACTORES CONFUSORES (fmri-confounds-extract)")
    print("=" * 70)
    check_conda_env(conda_env)

    confounds_out = base_dir / "derivatives" / "confounds"
    confounds_out.mkdir(parents=True, exist_ok=True)

    func_dir = base_dir / "fmriprep_output" / f"sub-{subj_id}" / "func"
    for task in tasks:
        tsv_path = func_dir / f"sub-{subj_id}_task-{task}_desc-confounds_timeseries.tsv"
        if not tsv_path.exists():
            print(f"[ERROR] No se encontró {tsv_path}")
            sys.exit(1)
        cmd = [
            "conda", "run", "-n", conda_env,
            "fmri-confounds-extract",
            "--inputfilepath", str(tsv_path),
            "--outputdir", str(confounds_out) + "/",
        ]
        run(cmd, f"Extracción de confounds para tarea '{task}'")

    print(f"\n[OK] Confounds generados en {confounds_out}")
    print("Nota: este script usa 'conda run' por comando, así que no hace "
          "falta desactivar el entorno manualmente antes de FSL (evita el "
          "conflicto que menciona la guía).")


# ---------------------------------------------------------------------------
# Paso 9b: FEAT
# ---------------------------------------------------------------------------

def choose_fsf_template(task: str) -> Path:
    if "viso" in task.lower():
        template = TEMPLATES_DIR / "150x30.fsf"
    else:
        template = TEMPLATES_DIR / "100x20.fsf"
    if not template.exists():
        print(f"[ERROR] No se encontró la plantilla {template}. Cópiala "
              f"desde tu carpeta institucional antes de continuar.")
        sys.exit(1)
    return template


def patch_fsf(template_path: Path, output_fsf_path: Path,
              feat_input_no_ext: str, feat_outputdir_no_ext: str,
              confound_file: Path):
    """Genera un .fsf a partir de la plantilla, fijando las claves estándar
    de FSL: feat_files(1), fmri(outputdir), fmri(confoundevs),
    confoundev_files(1). Si una clave ya existe en la plantilla se
    reemplaza; si no existe, se agrega al final."""

    lines = template_path.read_text().splitlines()

    replacements = {
        r'^\s*set feat_files\(1\)': f'set feat_files(1) "{feat_input_no_ext}"',
        r'^\s*set fmri\(outputdir\)': f'set fmri(outputdir) "{feat_outputdir_no_ext}"',
        r'^\s*set fmri\(confoundevs\)': 'set fmri(confoundevs) 1',
        r'^\s*set confoundev_files\(1\)': f'set confoundev_files(1) "{confound_file}"',
    }

    matched_keys = set()
    new_lines = []
    for line in lines:
        replaced = False
        for pattern, new_line in replacements.items():
            if re.match(pattern, line):
                new_lines.append(new_line)
                matched_keys.add(pattern)
                replaced = True
                break
        if not replaced:
            new_lines.append(line)

    for pattern, new_line in replacements.items():
        if pattern not in matched_keys:
            new_lines.append(new_line)

    output_fsf_path.write_text("\n".join(new_lines) + "\n")

    print(f"\nParámetros clave escritos en {output_fsf_path.name}:")
    for new_line in replacements.values():
        print(f"    {new_line}")


def run_feat(fsf_path: Path):
    run(["feat", str(fsf_path)], f"FEAT ({fsf_path.name})", capture=False)


def feat_qc(feat_output_dir: Path) -> bool:
    """QC automático: desplazamiento máximo relativo vs 1.5 mm, y búsqueda
    de la palabra 'error' en el log de FEAT. Devuelve True si pasa ambos
    chequeos automáticos (esto NO reemplaza la revisión visual de postats)."""
    ok = True

    rel_rms_path = feat_output_dir / "mc" / "prefiltered_func_data_mcf_rel.rms"
    if rel_rms_path.exists():
        values = [float(v) for v in rel_rms_path.read_text().split() if v.strip()]
        max_disp = max(values) if values else 0.0
        status = "OK" if max_disp <= MOTION_THRESHOLD_MM else "FALLA"
        print(f"[QC movimiento] Desplazamiento relativo máximo: "
              f"{max_disp:.3f} mm (umbral {MOTION_THRESHOLD_MM} mm) -> {status}")
        if max_disp > MOTION_THRESHOLD_MM:
            ok = False
    else:
        print(f"[AVISO] No se encontró {rel_rms_path}, no se pudo calcular "
              f"el QC de movimiento automáticamente.")

    log_path = feat_output_dir / "report_log.html"
    if log_path.exists():
        content = log_path.read_text(errors="ignore").lower()
        if "error" in content:
            print(f"[QC log] Se encontró la palabra 'error' en "
                  f"{log_path.name}. Revísalo antes de continuar.")
            ok = False
        else:
            print(f"[QC log] Sin errores en {log_path.name}.")
    else:
        print(f"[AVISO] No se encontró {log_path}.")

    return ok


def open_report(feat_output_dir: Path):
    report_path = feat_output_dir / "report.html"
    if report_path.exists():
        subprocess.call(["wslview", str(report_path)])
    else:
        print(f"[AVISO] No se encontró {report_path} para abrir.")


def step_feat(base_dir: Path, subj_id: str, tasks: list):
    print("\n" + "=" * 70)
    print("PASO 9b: FEAT CON EVs DE CONFUSIÓN")
    print("=" * 70)

    results = {}
    for task in tasks:
        print(f"\n--- Tarea: {task} ---")
        bold_path = get_preproc_bold_path(base_dir, subj_id, task)
        confound_file = get_confound_file(base_dir, task)
        template = choose_fsf_template(task)

        feat_input_no_ext = strip_nii_ext(bold_path)
        feat_outputdir_no_ext = feat_input_no_ext  # FEAT agrega .feat solo

        fsf_out = bold_path.parent / f"sub-{subj_id}_task-{task}_design.fsf"
        patch_fsf(template, fsf_out, feat_input_no_ext,
                  feat_outputdir_no_ext, confound_file)

        if not confirm(f"\n¿Los parámetros de arriba para '{task}' se ven "
                        f"correctos? (feat_files, outputdir, confound)"):
            print(f"[SALTADO] Tarea '{task}' no procesada. Ajusta el .fsf "
                  f"manualmente o corrige rutas y vuelve a correr.")
            continue

        run_feat(fsf_out)

        feat_output_dir = Path(feat_outputdir_no_ext + ".feat")
        print(f"\n>>> Revisión de calidad para '{task}'")
        auto_ok = feat_qc(feat_output_dir)
        open_report(feat_output_dir)

        print("\n[REVISIÓN OBLIGATORIA] En el reporte que se acaba de abrir:")
        print("  1. Prestats: confirma que el movimiento pasa el corte de 1.5 mm.")
        print("  2. Postats: confirma que la señal BOLD concuerda con el diseño "
              "experimental y que las activaciones son anatómicamente plausibles.")

        if not auto_ok:
            print(f"[AVISO] El QC automático detectó un posible problema en "
                  f"'{task}'. Revisa con cuidado antes de continuar.")

        passed = confirm(f"¿'{task}' pasa la validación de prestats/postats?")
        results[task] = {
            "feat_output_dir": feat_output_dir,
            "validated": passed,
        }
        if not passed:
            print(f"[MARCADO] Tarea '{task}' NO validada. No se dicomizará "
                  f"automáticamente a menos que la reproceses.")

    return results


# ---------------------------------------------------------------------------
# Paso 9c: Alineación / dicomización (nifti2dicom)
# ---------------------------------------------------------------------------

def guess_dcmref(base_dir: Path, subj_id: str) -> Path:
    dicom_dir = base_dir / "dicom" / f"sub-{subj_id}"
    candidates = sorted(dicom_dir.glob("*.dcm"))
    if not candidates:
        print(f"[ERROR] No se encontró ningún .dcm en {dicom_dir} para usar "
              f"como --dcmref. Pásalo explícitamente.")
        sys.exit(1)
    print(f"[AVISO] --dcmref no especificado. Usando por defecto: "
          f"{candidates[0].name}. Verifica que sea el DICOM correcto.")
    return candidates[0]


def get_anat_path_for_dicomize(base_dir: Path, subj_id: str, lesion: bool) -> Path:
    if lesion:
        anat_dir = base_dir / "anat_derivatives" / f"sub-{subj_id}" / "anat"
        candidates = list(anat_dir.glob(f"sub-{subj_id}_space-*_desc-preproc_T1w.nii.gz"))
    else:
        anat_dir = base_dir / "fmriprep_output" / f"sub-{subj_id}" / "anat"
        candidates = list(anat_dir.glob(f"sub-{subj_id}_space-*_desc-preproc_T1w.nii.gz"))
    if not candidates:
        print(f"[ERROR] No se encontró anatómica preprocesada en {anat_dir}.")
        sys.exit(1)
    return candidates[0]


def step_dicomize(base_dir: Path, subj_id: str, results: dict, conda_env: str,
                   dcmref: Path, vmin: float, alpha: float,
                   task_labels: dict):
    print("\n" + "=" * 70)
    print("PASO 9d: ALINEACIÓN AL ESPACIO ESTRUCTURAL Y DICOMIZACIÓN")
    print("=" * 70)
    check_conda_env(conda_env)

    lesion = has_lesion(base_dir, subj_id)
    if lesion:
        print("\n[ALERTA DE ESPACIO] Este paciente tiene lesión. La anatómica "
              "usada aquí viene del registro ANTs (MNI152NLin2009cAsym) "
              "mientras que las funcionales de fmriprep están en "
              "MNI152NLin6Asym. Verifica que ambos espacios sean compatibles "
              "para tu overlay antes de confiar en el resultado.")
        if not confirm("¿Confirmas que quieres continuar de todas formas?"):
            print("[DETENIDO] Corrige el espacio antes de dicomizar.")
            sys.exit(1)

    anat_path = get_anat_path_for_dicomize(base_dir, subj_id, lesion)
    print(f"[OK] Anatómica usada: {anat_path}")

    if dcmref is None:
        dcmref = guess_dcmref(base_dir, subj_id)

    out_dicom = base_dir / "out_dicom"
    out_dicom.mkdir(parents=True, exist_ok=True)

    for task, info in results.items():
        if not info.get("validated", False):
            print(f"\n[SALTADO] Tarea '{task}' no fue validada en el paso "
                  f"FEAT. No se dicomiza automáticamente.")
            continue

        feat_output_dir = info["feat_output_dir"]
        mask_path = feat_output_dir / "thresh_zstat1.nii.gz"
        if not mask_path.exists():
            print(f"[ERROR] No se encontró {mask_path} (thresh_zstat1, "
                  f"poststats) para la tarea '{task}'.")
            continue

        label = task_labels.get(task, f"Mapeo {task}")

        cmd = [
            "conda", "run", "-n", conda_env,
            "nifti2dicom",
            "--anat", str(anat_path),
            "--mask", str(mask_path),
            "--dcmref", str(dcmref),
            "--outputdir", str(out_dicom),
            "--text", label,
            "--vmin", str(vmin),
            "--alpha", str(alpha),
        ]
        run(cmd, f"Dicomización de '{task}'")

    print(f"\n[OK] Salidas DICOM generadas en {out_dicom}")
    print("\n[PASOS MANUALES PENDIENTES — no automatizados]")
    print("  1. Verifica el resultado en el directorio de salida con tu "
          "visualizador DICOM de preferencia.")
    print("  2. Importa en syngo.via y valida que coincida la información "
          "del paciente.")
    print("  3. Exporta a AGFA.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Automatiza FEAT + confounds + dicomización (paso 9 de la guía)."
    )
    parser.add_argument("--patient", required=True)
    parser.add_argument("--base-dir", default=str(CFG.funcionales_dir))
    parser.add_argument("--subject-id", default=CFG.subject_id)
    parser.add_argument("--steps", default="all",
                         help=f"{','.join(VALID_STEPS)} o 'all' (default: all)")
    parser.add_argument("--tasks", default=None,
                         help="Lista de tareas separadas por coma para "
                              "procesar solo esas (default: todas las detectadas)")
    parser.add_argument("--confound-env", default=CFG.conda_env,
                         help=f"Nombre del entorno conda (default: {CFG.conda_env})")
    parser.add_argument("--dcmref", default=None,
                         help="Ruta a un .dcm de referencia para nifti2dicom "
                              "(default: autodetecta el primero en dicom/sub-XX)")
    parser.add_argument("--vmin", type=float, default=ZSTAT_THRESHOLD_DEFAULT,
                         help=f"Umbral Z para nifti2dicom (default: {ZSTAT_THRESHOLD_DEFAULT})")
    parser.add_argument("--alpha", type=float, default=ALPHA_DEFAULT,
                         help=f"Transparencia del overlay (default: {ALPHA_DEFAULT})")
    return parser.parse_args()


def resolve_steps(steps_arg: str) -> list:
    if steps_arg.strip().lower() == "all":
        return VALID_STEPS
    requested = [s.strip() for s in steps_arg.split(",") if s.strip()]
    invalid = [s for s in requested if s not in VALID_STEPS]
    if invalid:
        print(f"[ERROR] Pasos inválidos: {invalid}. Válidos: {VALID_STEPS + ['all']}")
        sys.exit(1)
    return requested


def main():
    args = parse_args()
    base_dir = Path(args.base_dir) / args.patient
    steps = resolve_steps(args.steps)

    print(f"Paciente: {args.patient}")
    print(f"BASE_DIR: {base_dir}")
    print(f"SUBJ_ID:  {args.subject_id}")
    print(f"Pasos a ejecutar: {steps}")

    all_tasks = get_functional_tasks(base_dir, args.subject_id)
    tasks = ([t.strip() for t in args.tasks.split(",")]
              if args.tasks else all_tasks)

    results = {}

    if "confounds" in steps:
        step_confounds(base_dir, args.subject_id, tasks, args.confound_env)

    if "feat" in steps:
        results = step_feat(base_dir, args.subject_id, tasks)

    if "dicomize" in steps:
        if not results:
            # Si se corre --steps dicomize solo, asumimos que las tareas ya
            # fueron validadas manualmente en una corrida previa de 'feat'.
            print("\n[AVISO] Corriendo 'dicomize' sin haber corrido 'feat' en "
                  "esta misma ejecución. Se asume que ya validaste cada "
                  "tarea manualmente.")
            for task in tasks:
                bold_path = get_preproc_bold_path(base_dir, args.subject_id, task)
                feat_output_dir = Path(strip_nii_ext(bold_path) + ".feat")
                if not feat_output_dir.exists():
                    print(f"[ERROR] No existe {feat_output_dir} para '{task}'. "
                          f"Corre --steps feat primero.")
                    sys.exit(1)
                results[task] = {"feat_output_dir": feat_output_dir, "validated": True}

        dcmref = Path(args.dcmref) if args.dcmref else None
        task_labels = {}  # Personaliza aquí si quieres etiquetas más legibles
        step_dicomize(base_dir, args.subject_id, results, args.confound_env,
                      dcmref, args.vmin, args.alpha, task_labels)

    print("\n" + "=" * 70)
    print("PIPELINE FEAT + DICOMIZACIÓN COMPLETADO")
    print("=" * 70)


if __name__ == "__main__":
    main()
