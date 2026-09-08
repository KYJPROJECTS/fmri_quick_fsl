#!/usr/bin/env python3
"""
configure.py
=============
Asistente interactivo para crear o actualizar pipeline.config.ini — el
archivo de configuración que usan TODOS los scripts de este repo
(quick_qc/ y full_pipeline/): carpetas de entrada/salida, entorno conda,
umbrales de QC y las imágenes Docker a usar.

Uso (desde la raíz del repo):

    python3 configure.py

Vuelve a correrlo cuando algo cambie (nueva ruta de templates, cambio de
computador, etc.) — cada pregunta muestra tu valor actual como default,
así que basta con Enter para dejarlo igual.

pipeline.config.ini queda excluido de git (.gitignore) porque contiene
rutas específicas de tu máquina.
"""

import configparser

from common.config import DEFAULTS, CONFIG_PATH, load_config


def ask(prompt: str, default) -> str:
    resp = input(f"{prompt}\n  [{default}]: ").strip()
    return resp if resp else str(default)


def main():
    print("=" * 70)
    print("CONFIGURACIÓN DEL PIPELINE FMRI")
    print("=" * 70)
    print(f"Esto va a crear/actualizar: {CONFIG_PATH}\n")

    # Si ya existe una configuración, la usamos como default de cada
    # pregunta en vez de los defaults "de fábrica".
    current = load_config() if CONFIG_PATH.exists() else None
    d = DEFAULTS

    print("--- Rutas ---")
    funcionales_dir = ask(
        "Carpeta raíz de pacientes (funcionales/)",
        current.funcionales_dir if current else d["paths"]["funcionales_dir"],
    )
    templates_dir = ask(
        "Carpeta con los .fsf reales (100x20.fsf, 150x30.fsf)",
        current.templates_dir if current else d["paths"]["templates_dir"],
    )
    license_path = ask(
        "Ruta a la licencia de FreeSurfer",
        current.license_path if current else d["paths"]["license_path"],
    )
    mango_exe = ask(
        "Ruta al ejecutable de Mango (Windows)",
        current.mango_exe if current else d["paths"]["mango_exe"],
    )

    print("\n--- Entorno ---")
    conda_env = ask(
        "Entorno conda con fmri-confounds-extract y nifti2dicom",
        current.conda_env if current else d["environment"]["conda_env"],
    )
    subject_id = ask(
        "ID de sujeto BIDS por defecto",
        current.subject_id if current else d["environment"]["subject_id"],
    )

    print("\n--- Umbrales de QC ---")
    motion_mm = ask(
        "Umbral de movimiento máximo aceptable (mm)",
        current.motion_threshold_mm if current else d["thresholds"]["motion_mm"],
    )
    zstat_vmin = ask(
        "Umbral Z mínimo para activaciones (nifti2dicom --vmin)",
        current.zstat_vmin if current else d["thresholds"]["zstat_vmin"],
    )
    alpha = ask(
        "Transparencia del overlay, 0-1 (nifti2dicom --alpha)",
        current.alpha if current else d["thresholds"]["alpha"],
    )

    print("\n--- Imágenes Docker ---")
    bidscoin_image = ask("Imagen de bidscoin",
                          current.bidscoin_image if current else d["docker"]["bidscoin_image"])
    fmriprep_image = ask("Imagen de fmriprep",
                          current.fmriprep_image if current else d["docker"]["fmriprep_image"])
    validator_image = ask("Imagen de bids-validator",
                           current.validator_image if current else d["docker"]["validator_image"])
    ants_image = ask("Imagen de ANTs",
                      current.ants_image if current else d["docker"]["ants_image"])

    parser = configparser.ConfigParser()
    parser["paths"] = {
        "funcionales_dir": funcionales_dir,
        "templates_dir": templates_dir,
        "license_path": license_path,
        "mango_exe": mango_exe,
    }
    parser["environment"] = {
        "conda_env": conda_env,
        "subject_id": subject_id,
    }
    parser["thresholds"] = {
        "motion_mm": str(motion_mm),
        "zstat_vmin": str(zstat_vmin),
        "alpha": str(alpha),
    }
    parser["docker"] = {
        "bidscoin_image": bidscoin_image,
        "fmriprep_image": fmriprep_image,
        "validator_image": validator_image,
        "ants_image": ants_image,
    }

    with open(CONFIG_PATH, "w") as f:
        parser.write(f)

    print(f"\n[OK] Configuración guardada en {CONFIG_PATH}")
    print("Este archivo NO se sube a GitHub (está en .gitignore).")
    print("\nYa puedes correr los scripts sin pasar --base-dir, --templates-dir, "
          "etc. cada vez — usarán estos valores por defecto.")


if __name__ == "__main__":
    main()
