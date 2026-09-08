#!/usr/bin/env python3
"""
run_bids_fmriprep.py
=====================
Automatiza el bloque BIDS + fmriprep del pipeline completo de procesamiento
fMRI descrito en "Guía de procesamiento FMRI" (pasos 1 a 8):

    1. Definición de variables de ruta
    2. BIDSMAPPER (mapeo DICOM -> BIDS)          [requiere GUI, checkpoint]
    3. BIDSCOINER (conversión DICOM -> BIDS)      [automático]
    4. BIDSVALIDATOR                              [automático, para en error]
    5. Preprocesamiento de la lesión (fsleyes)    [manual, checkpoint]
    6. Creación de .bidsignore                    [automático, solo si hay lesión]
    7. Registro ANTs a espacio MNI152 (lesión)    [automático, solo si hay lesión]
    8. fmriprep (rama CON/SIN lesión)             [automático]

USO BÁSICO
----------
    python3 run_bids_fmriprep.py --patient "ArangoValenciaKarenNicolle"

Esto asume la misma convención de carpetas que run_patient.py:
    funcionales/[NombreCompletoDelPaciente]/
        dicom/sub-01/               <- DICOM crudo (ya debe existir)
        bids_output/
        fmriprep_output/
        anat_derivatives/
        work/
        out_dicom/

REANUDAR DESPUÉS DE UN FALLO
-----------------------------
Los pasos de ANTs (45-90 min) y fmriprep (horas) son largos. Si el pipeline
se cae a mitad de fmriprep, no tiene sentido repetir bidscoiner/bidsvalidator.
Usa --steps para correr solo lo que falta, por ejemplo:

    python3 run_bids_fmriprep.py --patient "..." --steps fmriprep

Pasos válidos para --steps (separados por coma, en cualquier combinación):
    bidsmapper, bidscoiner, bidsvalidator, lesion, fmriprep
    (o "all", que es el default y corre todo en orden)

SUPUESTOS NO VERIFICADOS (léelos antes de usar en un paciente real)
--------------------------------------------------------------------
1. DISPLAY y un servidor X11 (p. ej. VcXsrv) ya están configurados en tu
   máquina Windows para que el contenedor de bidsmapper pueda abrir su GUI
   desde WSL. Si bidsmapper no abre ventana, el problema probablemente es
   este, no el script.
2. El archivo `templates/run_ants_lesion.sh` (copiado literal de tu guía)
   debe existir junto a este script. Si no existe, el paso "lesion" falla
   con un mensaje explícito en vez de generar el script sobre la marcha.
3. Los comandos de docker (bidscoiner, bidsvalidator, fmriprep) están
   copiados literalmente de la guía. No se inventó ningún flag adicional.
4. `docker` y `feat`/FSL corren en el entorno de WSL (Ubuntu), igual que
   run_patient.py.

LO QUE ESTE SCRIPT DELIBERADAMENTE NO AUTOMATIZA
--------------------------------------------------
- La edición manual del YAML de bidsmapper (borrar el campo "acq"): se
  abre la GUI y se espera confirmación del usuario. Automatizar esa edición
  de texto sin verificar que la nomenclatura de secuencias del resonador es
  estable entre pacientes podría romper el mapeo de forma silenciosa.
- La segmentación de la lesión en fsleyes: es un acto de juicio clínico.
  El script solo verifica que el archivo de máscara resultante exista con
  el nombre esperado antes de continuar.
- La descarga desde syngo.via y la exportación a AGFA: software propietario
  sin API scriptable disponible.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Bootstrap para poder importar common/ estando en una subcarpeta del repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.config import load_config

CFG = load_config()

# ---------------------------------------------------------------------------
# Configuración (viene de pipeline.config.ini vía common/config.py)
# ---------------------------------------------------------------------------

# Nota: run_ants_lesion.sh es específico de este bloque (full_pipeline),
# no se comparte con quick_qc, por eso vive en su propia templates/ local
# en vez de en CFG.templates_dir (que es para los .fsf compartidos).
ANTS_LESION_TEMPLATE = Path(__file__).resolve().parent / "templates" / "run_ants_lesion.sh"

DOCKER_IMAGES = [
    CFG.bidscoin_image,
    CFG.fmriprep_image,
    CFG.validator_image,
    CFG.ants_image,
]

VALID_STEPS = ["bidsmapper", "bidscoiner", "bidsvalidator", "lesion", "fmriprep"]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def run(cmd, description, check=True):
    """Ejecuta un comando mostrando qué se está corriendo. Devuelve el
    CompletedProcess. Si check=True, termina el script en caso de error
    (código de salida distinto de 0)."""
    print(f"\n>>> {description}")
    print(f"    $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if check and result.returncode != 0:
        print(f"\n[ERROR] El paso '{description}' terminó con código "
              f"{result.returncode}. Deteniendo el pipeline.", file=sys.stderr)
        sys.exit(result.returncode)
    return result


def confirm(prompt):
    """Checkpoint interactivo. Devuelve True solo si el usuario escribe
    's' o 'si'."""
    answer = input(f"{prompt} [s/n]: ").strip().lower()
    return answer in ("s", "si", "sí", "y", "yes")


def check_docker_running():
    result = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if result.returncode != 0:
        print("[ERROR] Docker no está corriendo o no es accesible desde WSL.")
        print("        Abre Docker Desktop en Windows y confirma que el "
              "servicio esté activo, luego vuelve a correr este script.")
        sys.exit(1)
    print("[OK] Docker está corriendo.")


def check_and_pull_images():
    """Verifica localmente cada imagen requerida y hace pull solo si falta,
    para que el pull no ocurra de forma silenciosa en medio de un paso
    largo (p. ej. durante fmriprep)."""
    for image in DOCKER_IMAGES:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"[INFO] Imagen '{image}' no encontrada localmente. "
                  f"Descargando...")
            run(["docker", "pull", image], f"Pull de {image}")
        else:
            print(f"[OK] Imagen '{image}' ya está disponible localmente.")


# ---------------------------------------------------------------------------
# Estructura de carpetas
# ---------------------------------------------------------------------------

def setup_directories(base_dir: Path) -> dict:
    paths = {
        "dicom": base_dir / "dicom",
        "bids_output": base_dir / "bids_output",
        "fmriprep_output": base_dir / "fmriprep_output",
        "anat_derivatives": base_dir / "anat_derivatives",
        "work": base_dir / "work",
        "out_dicom": base_dir / "out_dicom",
    }
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)

    print("\nEstructura de carpetas del paciente:")
    for name, path in paths.items():
        print(f"  {name:<18} {path}")

    if not any((paths["dicom"] / "sub-01").glob("*.dcm")):
        print(f"\n[AVISO] No se encontraron archivos .dcm en "
              f"{paths['dicom'] / 'sub-01'}. Confirma que ya exportaste "
              f"las secuencias desde syngo.via antes de continuar.")
    return paths


# ---------------------------------------------------------------------------
# Paso 2: BIDSMAPPER
# ---------------------------------------------------------------------------

def step_bidsmapper(base_dir: Path):
    print("\n" + "=" * 70)
    print("PASO 2: BIDSMAPPER (mapeo DICOM -> BIDS)")
    print("=" * 70)
    print("Se abrirá la GUI de bidsmapper. Recuerda:")
    print("  1. Clic en 'edit' sobre la secuencia anatómica.")
    print("  2. Borrar el campo 'acq' dejándolo en blanco.")
    print("  3. OK -> SAVE -> SAVE en la ventana emergente -> cerrar GUI.")

    cmd = [
        "docker", "run", "-ti", "--rm",
        "-e", "DISPLAY=$DISPLAY",
        "-v", "/tmp/.X11-unix:/tmp/.X11-unix",
        "-v", f"{base_dir}/dicom:/data/raw:ro",
        "-v", f"{base_dir}/bids_output:/data/bids",
        CFG.bidscoin_image,
        "bidsmapper", "/data/raw", "/data/bids",
    ]
    # Nota: se ejecuta con subprocess.call (no run()) porque es interactivo
    # y necesita heredar la terminal/X11 directamente.
    print(f"\n>>> Lanzando bidsmapper (interactivo)")
    print(f"    $ {' '.join(cmd)}")
    subprocess.call(cmd)

    if not confirm("\n¿Guardaste el bidsmap.yaml correctamente (SAVE x2)?"):
        print("[DETENIDO] Vuelve a correr --steps bidsmapper cuando lo hayas "
              "completado.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Paso 3: BIDSCOINER
# ---------------------------------------------------------------------------

def step_bidscoiner(base_dir: Path):
    print("\n" + "=" * 70)
    print("PASO 3: BIDSCOINER (conversión DICOM -> BIDS)")
    print("=" * 70)
    cmd = [
        "docker", "run", "-ti", "--rm",
        "-v", f"{base_dir}/dicom:/data/raw:ro",
        "-v", f"{base_dir}/bids_output:/data/bids",
        CFG.bidscoin_image,
        "bidscoiner", "/data/raw", "/data/bids",
    ]
    run(cmd, "BIDSCOINER")


# ---------------------------------------------------------------------------
# Paso 4: BIDSVALIDATOR
# ---------------------------------------------------------------------------

def step_bidsvalidator(base_dir: Path):
    print("\n" + "=" * 70)
    print("PASO 4: BIDSVALIDATOR")
    print("=" * 70)
    cmd = [
        "docker", "run", "-ti", "--rm",
        "-v", f"{base_dir}/bids_output:/data:ro",
        CFG.validator_image, "/data",
    ]
    # No usamos check=True: queremos inspeccionar la salida nosotros mismos,
    # porque el validador puede devolver código != 0 incluso solo con
    # warnings, y la guía dice explícitamente que los warnings no detienen
    # el flujo.
    result = run(cmd, "BIDSVALIDATOR", check=False)
    output = (result.stdout or "") + (result.stderr or "")
    output_lower = output.lower()

    # Búsqueda simple de la palabra "error" como indicador de error real.
    # Esto es una heurística de texto, no un parseo del JSON estructurado
    # del validador (bids-validator no siempre expone --json de forma
    # consistente entre versiones), así que ante cualquier duda revisa el
    # log completo impreso arriba.
    has_errors = "error" in output_lower and "0 errors" not in output_lower

    if has_errors:
        print("\n[ERROR] BIDSVALIDATOR reportó errores (no solo warnings).")
        print("        Revisa el log completo arriba antes de continuar.")
        sys.exit(1)
    else:
        print("\n[OK] BIDSVALIDATOR no reportó errores bloqueantes "
              "(puede haber warnings, que según la guía son aceptables).")


# ---------------------------------------------------------------------------
# Paso 5-7: Lesión (checkpoint fsleyes + .bidsignore + ANTs)
# ---------------------------------------------------------------------------

def detect_lesion_mask(base_dir: Path, subj_id: str) -> Path:
    return base_dir / "bids_output" / f"sub-{subj_id}" / "anat" / \
        f"sub-{subj_id}_label-lesion_mask.nii.gz"


def create_bidsignore(base_dir: Path):
    bidsignore_path = base_dir / "bids_output" / ".bidsignore"
    content = (
        "extra_data/\n"
        "sub-*_ct.*\n"
        "*_label-lesion_roi.nii.gz\n"
        "*_label-lesion_mask.nii.gz\n"
    )
    bidsignore_path.write_text(content)
    print(f"[OK] .bidsignore creado en {bidsignore_path}")


def step_lesion(base_dir: Path, subj_id: str) -> bool:
    """Devuelve True si el paciente tiene lesión y el pipeline de ANTs se
    ejecutó correctamente, False si el paciente no tiene lesión."""
    print("\n" + "=" * 70)
    print("PASO 5-7: PREPROCESAMIENTO DE LA LESIÓN (si aplica)")
    print("=" * 70)

    has_lesion = confirm("¿Este paciente tiene lesión cerebral?")
    if not has_lesion:
        print("[OK] Paciente sin lesión. Se salta ANTs y se usará la rama "
              "estándar de fmriprep.")
        return False

    mask_path = detect_lesion_mask(base_dir, subj_id)
    print(f"\nBuscando máscara de lesión en:\n  {mask_path}")

    while not mask_path.exists():
        print(f"\n[DETENIDO] No se encontró {mask_path.name}.")
        print("Pasos manuales pendientes (fsleyes):")
        print("  1. Abre fsleyes y carga la imagen anatómica.")
        print("  2. Tools -> Edit mode -> crea imagen 3D vacía (_mask).")
        print("  3. Segmenta la lesión con threshold + fill selected voxels.")
        print(f"  4. Guarda la máscara exactamente como: {mask_path.name}")
        print(f"     en la carpeta: {mask_path.parent}")
        if not confirm("\n¿Ya guardaste la máscara con ese nombre exacto?"):
            print("[DETENIDO] Vuelve a correr --steps lesion cuando la "
                  "máscara exista.")
            sys.exit(1)

    print(f"[OK] Máscara de lesión encontrada: {mask_path}")

    create_bidsignore(base_dir)

    if not ANTS_LESION_TEMPLATE.exists():
        print(f"\n[ERROR] No se encontró la plantilla en "
              f"{ANTS_LESION_TEMPLATE}.")
        print("        Copia ahí tu run_ants_lesion.sh (el de la guía, con "
              "SUBJ parametrizado) antes de continuar.")
        sys.exit(1)

    target_script = base_dir / "run_ants_lesion.sh"
    shutil.copy(ANTS_LESION_TEMPLATE, target_script)
    target_script.chmod(0o755)

    print(f"\n>>> Ejecutando registro ANTs (45-90 min estimados)...")
    run([str(target_script), str(base_dir)], "Registro ANTs a espacio MNI152")

    warped_path = base_dir / "work" / f"sub-{subj_id}" / "ants" / \
        f"sub-{subj_id}_to_MNI_Warped.nii.gz"
    print(f"\n[REVISIÓN OBLIGATORIA] Abre en FSLeyes:")
    print(f"  {warped_path}")
    print("  Agrega el template MNI152_T1_1mm.nii.gz (File -> Add Standard) "
          "y verifica visualmente que el registro sea correcto.")
    print("  Un registro fallido invalida todos los análisis posteriores.")

    if not confirm("\n¿El registro se ve anatómicamente correcto?"):
        print("[DETENIDO] Corrige el registro antes de continuar con "
              "fmriprep. No se recomienda seguir con un registro fallido.")
        sys.exit(1)

    return True


# ---------------------------------------------------------------------------
# Paso 8: fmriprep
# ---------------------------------------------------------------------------

def step_fmriprep(base_dir: Path, subj_id: str, license_path: Path,
                   has_lesion: bool, n_cpus: int, mem_mb: int):
    print("\n" + "=" * 70)
    print(f"PASO 8: FMRIPREP (rama {'CON' if has_lesion else 'SIN'} lesión)")
    print("=" * 70)

    if not license_path.exists():
        print(f"[ERROR] No se encontró la licencia de FreeSurfer en "
              f"{license_path}.")
        print("        Descárgala en surfer.nmr.mgh.harvard.edu y guárdala "
              "en esa ruta.")
        sys.exit(1)

    templateflow_dir = Path.home() / ".cache" / "templateflow"
    templateflow_dir.mkdir(parents=True, exist_ok=True)

    if has_lesion:
        cmd = [
            "docker", "run", "-ti", "--rm",
            "-v", f"{base_dir}/bids_output:/data:ro",
            "-v", f"{base_dir}/fmriprep_output:/out",
            "-v", f"{base_dir}/anat_derivatives:/anat_deriv:ro",
            "--tmpfs", "/work:exec,mode=777",
            "-v", f"{license_path}:/opt/freesurfer/license.txt:ro",
            # SIN :ro -- la guía indica que debe poder escribir en
            # templateflow para descargas adicionales en la rama con lesión.
            "-v", f"{templateflow_dir}:/templateflow",
            "-e", "TEMPLATEFLOW_HOME=/templateflow",
            CFG.fmriprep_image,
            "/data", "/out", "participant",
            "--participant-label", subj_id,
            "--skip_bids_validation",
            "-d", "fmriprep=/anat_deriv",
            "--work-dir", "/work",
            "--fs-no-reconall",
            "--output-spaces", "MNI152NLin6Asym:res-2", "T1w",
            "--n_cpus", str(n_cpus),
            "--mem", str(mem_mb),
            "--low-mem",
            "--notrack",
        ]
    else:
        cmd = [
            "docker", "run", "-ti", "--rm",
            "-v", f"{base_dir}/bids_output:/data:ro",
            "-v", f"{base_dir}/fmriprep_output:/out",
            "--tmpfs", "/work:exec,mode=777",
            "-v", f"{license_path}:/opt/freesurfer/license.txt:ro",
            "-v", f"{templateflow_dir}:/templateflow:ro",
            "-e", "TEMPLATEFLOW_HOME=/templateflow",
            CFG.fmriprep_image,
            "/data", "/out", "participant",
            "--participant-label", subj_id,
            "--work-dir", "/work",
            "--fs-no-reconall",
            "--output-spaces", "MNI152NLin6Asym:res-2", "T1w",
            "--n_cpus", str(n_cpus),
            "--mem", str(mem_mb),
            "--low-mem",
            "--notrack",
        ]

    run(cmd, "fmriprep (esto puede tardar horas)")
    print("\n[OK] fmriprep terminó sin errores de proceso. Esto NO significa "
          "que el preprocesamiento sea válido: revisa los reportes HTML de "
          "fmriprep en fmriprep_output/ antes de seguir con FEAT.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Automatiza el bloque BIDS + fmriprep del pipeline fMRI."
    )
    parser.add_argument("--patient", required=True,
                         help="Nombre completo del paciente (nombre de la "
                              "subcarpeta en funcionales/).")
    parser.add_argument("--base-dir", default=str(CFG.funcionales_dir),
                         help=f"Carpeta raíz de pacientes "
                              f"(default: {CFG.funcionales_dir})")
    parser.add_argument("--subject-id", default=CFG.subject_id,
                         help=f"ID BIDS del sujeto (default: {CFG.subject_id})")
    parser.add_argument("--license-path",
                         default=str(CFG.license_path),
                         help=f"Ruta a la licencia de FreeSurfer "
                              f"(default: {CFG.license_path})")
    parser.add_argument("--steps", default="all",
                         help=f"Pasos a ejecutar, separados por coma: "
                              f"{','.join(VALID_STEPS)}, o 'all' (default: all)")
    parser.add_argument("--n-cpus", type=int, default=6,
                         help="--n_cpus para fmriprep (default: 6)")
    parser.add_argument("--mem-mb", type=int, default=20000,
                         help="--mem para fmriprep en MB (default: 20000)")
    return parser.parse_args()


def resolve_steps(steps_arg: str) -> list:
    if steps_arg.strip().lower() == "all":
        return VALID_STEPS
    requested = [s.strip() for s in steps_arg.split(",") if s.strip()]
    invalid = [s for s in requested if s not in VALID_STEPS]
    if invalid:
        print(f"[ERROR] Pasos inválidos: {invalid}. "
              f"Válidos: {VALID_STEPS + ['all']}")
        sys.exit(1)
    return requested


def main():
    args = parse_args()
    base_dir = Path(args.base_dir) / args.patient
    license_path = Path(args.license_path)
    steps = resolve_steps(args.steps)

    print(f"Paciente: {args.patient}")
    print(f"BASE_DIR: {base_dir}")
    print(f"SUBJ_ID:  {args.subject_id}")
    print(f"Pasos a ejecutar: {steps}")

    check_docker_running()
    check_and_pull_images()
    setup_directories(base_dir)

    has_lesion = detect_lesion_mask(base_dir, args.subject_id).exists()

    if "bidsmapper" in steps:
        step_bidsmapper(base_dir)
    if "bidscoiner" in steps:
        step_bidscoiner(base_dir)
    if "bidsvalidator" in steps:
        step_bidsvalidator(base_dir)
    if "lesion" in steps:
        has_lesion = step_lesion(base_dir, args.subject_id)
    if "fmriprep" in steps:
        step_fmriprep(base_dir, args.subject_id, license_path, has_lesion,
                      args.n_cpus, args.mem_mb)

    print("\n" + "=" * 70)
    print("PIPELINE BIDS + FMRIPREP COMPLETADO")
    print("=" * 70)
    print("Siguiente paso manual: revisar reportes de fmriprep, luego "
          "continuar con el bloque de FEAT (script pendiente).")


if __name__ == "__main__":
    main()
