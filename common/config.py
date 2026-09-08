"""
common/config.py
==================
Configuración compartida por todos los scripts de este repo
(quick_qc/ y full_pipeline/).

Orden de precedencia (mayor a menor):
    1. Un --config-file explícito pasado al script (si el script lo soporta)
    2. pipeline.config.ini en la raíz del repo (NO versionado — cada quien
       lo crea/edita con `python3 configure.py`)
    3. Los valores por defecto de este archivo

Cada script hace algo como:

    cfg = load_config()
    parser.add_argument("--base-dir", default=str(cfg.funcionales_dir))

Así, si no pasas el flag, se usa lo que hayas configurado; si lo pasas
explícitamente en la línea de comandos, ese valor gana siempre (es el
comportamiento normal de argparse).
"""

import configparser
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILENAME = "pipeline.config.ini"
CONFIG_PATH = REPO_ROOT / CONFIG_FILENAME
EXAMPLE_CONFIG_PATH = REPO_ROOT / "pipeline.config.example.ini"

DEFAULTS = {
    "paths": {
        "funcionales_dir": "/mnt/c/Users/karen/Desktop/funcionales",
        "templates_dir": str(REPO_ROOT / "templates"),
        "license_path": str(Path.home() / "license.txt"),
        "mango_exe": "/mnt/c/Program Files/Mango/Mango.exe",
    },
    "environment": {
        "conda_env": "bioimg",
        "subject_id": "01",
    },
    "thresholds": {
        "motion_mm": "1.5",
        "zstat_vmin": "2.3",
        "alpha": "0.8",
    },
    "docker": {
        "bidscoin_image": "marcelzwiers/bidscoin:latest",
        "fmriprep_image": "nipreps/fmriprep:latest",
        "validator_image": "bids/validator:latest",
        "ants_image": "antsx/ants:latest",
    },
}


@dataclass
class PipelineConfig:
    funcionales_dir: Path
    templates_dir: Path
    license_path: Path
    mango_exe: str
    conda_env: str
    subject_id: str
    motion_threshold_mm: float
    zstat_vmin: float
    alpha: float
    bidscoin_image: str
    fmriprep_image: str
    validator_image: str
    ants_image: str


def _parser_with_defaults() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read_dict(DEFAULTS)
    return parser


def load_config(config_path: Path = None) -> PipelineConfig:
    """Carga la configuración. Si config_path no existe, usa los defaults
    de este archivo y avisa (no falla) para que los scripts sigan siendo
    usables sin configuración previa."""
    path = Path(config_path) if config_path else CONFIG_PATH
    parser = _parser_with_defaults()

    if path.exists():
        parser.read(path)
    else:
        print(f"[AVISO] No se encontró {path}. Usando valores por defecto.")
        print(f"        Corre 'python3 configure.py' desde la raíz del "
              f"repo para crear tu configuración.")

    return PipelineConfig(
        funcionales_dir=Path(parser.get("paths", "funcionales_dir")),
        templates_dir=Path(parser.get("paths", "templates_dir")),
        license_path=Path(parser.get("paths", "license_path")).expanduser(),
        mango_exe=parser.get("paths", "mango_exe"),
        conda_env=parser.get("environment", "conda_env"),
        subject_id=parser.get("environment", "subject_id"),
        motion_threshold_mm=parser.getfloat("thresholds", "motion_mm"),
        zstat_vmin=parser.getfloat("thresholds", "zstat_vmin"),
        alpha=parser.getfloat("thresholds", "alpha"),
        bidscoin_image=parser.get("docker", "bidscoin_image"),
        fmriprep_image=parser.get("docker", "fmriprep_image"),
        validator_image=parser.get("docker", "validator_image"),
        ants_image=parser.get("docker", "ants_image"),
    )
