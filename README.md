# fmri_quick_fsl

Automatización de línea de comandos para el pipeline fMRI completo, desde
el QC rápido en sitio hasta la conversión BIDS, fmriprep, FEAT y
dicomización final. Todo configurable desde terminal, sin editar código.

## Qué hay aquí

Dos bloques independientes, pensados para momentos distintos del flujo:

| Carpeta | Cuándo se usa | Qué hace |
|---|---|---|
| `quick_qc/` | Con el paciente todavía en el resonador | QC rápido en sitio: espera DICOM → convierte a NIfTI → detecta tareas → corre FEAT → valida activaciones (Paso 2 del protocolo) |
| `full_pipeline/` | Después, en el procesamiento completo de investigación | BIDS → fmriprep → FEAT con confounds → dicomización de activaciones (Paso 3 del protocolo) |

## Configuración (nuevo)

Todos los scripts leen sus rutas y parámetros de un único archivo,
`pipeline.config.ini`, en vez de tenerlos hardcodeados por separado. Se
crea con un asistente interactivo:

```bash
python3 configure.py
```

Te pregunta, con tu valor actual como default (Enter para dejarlo igual):

- Carpeta raíz de pacientes (`funcionales/`)
- Carpeta con los `.fsf` reales (`100x20.fsf`, `150x30.fsf`)
- Ruta a la licencia de FreeSurfer
- Ruta al ejecutable de Mango
- Entorno conda (`fmri-confounds-extract`, `nifti2dicom`)
- ID de sujeto BIDS por defecto
- Umbrales de QC (movimiento máximo, Z mínimo, alpha del overlay)
- Imágenes Docker a usar

Esto genera `pipeline.config.ini`, que **no se sube a GitHub** (está en
`.gitignore`) porque contiene rutas de tu máquina. `pipeline.config.example.ini`
sí está versionado, como referencia.

Cualquier script sigue aceptando sus propios flags (`--base-dir`,
`--templates-dir`, etc.) — si los pasas explícitamente, ganan sobre el
config file. Si no los pasas, usa lo que hayas configurado.

Vuelve a correr `configure.py` cuando algo cambie (nuevo computador, nueva
ruta de templates, etc.).

## Instalación

```bash
git clone https://github.com/KYJPROJECTS/fmri_quick_fsl.git
cd fmri_quick_fsl
python3 configure.py
```

Coloca tus `.fsf` reales (`100x20.fsf`, `150x30.fsf`) en la carpeta que
hayas indicado como `templates_dir` — no vienen en el repo.

## Uso — quick_qc/ (QC rápido en el equipo)

```bash
python3 quick_qc/quick_feat.py --patient "ApellidoNombrePaciente"
```

Flujo: confirma carpetas → espera DICOM → convierte con `dcm2niix` →
detecta tareas funcionales → revisa/corrige asignación de `.fsf` → corre
FEAT secuencialmente, abriendo el reporte de cada tarea automáticamente.

```bash
python3 quick_qc/open_mango.py --patient "ApellidoNombrePaciente"
```

Abre en Mango cada tarea completada (anatómica + máscara de activación),
mostrando el umbral Z real usado por FSL.

**Arreglado en esta versión:**
- Valida de verdad que `fmri(npts)` del template coincida con el número
  real de volúmenes del NIfTI (antes el README lo prometía pero el código
  no lo hacía). Requiere `nibabel` instalado — si falta, avisa y sigue sin
  bloquear.
- `quick_feat.py` ahora abre el reporte de cada tarea automáticamente vía
  `wslview` al terminar (antes había que abrirlo a mano o esperar a
  `open_mango.py`).

## Uso — full_pipeline/ (procesamiento completo)

```bash
python3 full_pipeline/run_bids_fmriprep.py --patient "ApellidoNombrePaciente"
python3 full_pipeline/run_feat_dicomize.py --patient "ApellidoNombrePaciente"
```

Reanudar por bloques (útil porque ANTs y fmriprep tardan horas):

```bash
python3 full_pipeline/run_bids_fmriprep.py --patient "..." --steps fmriprep
python3 full_pipeline/run_feat_dicomize.py --patient "..." --steps feat --tasks lenguajehistoria
```

Ver todas las opciones con `--help` en cualquiera de los dos scripts.

### Qué automatiza `run_bids_fmriprep.py` (pasos 1-8 de la guía)
- BIDSMAPPER: abre la GUI, checkpoint de confirmación (edición manual del
  YAML no automatizada a propósito).
- BIDSCOINER, BIDSVALIDATOR: automáticos.
- Detección de lesión vía existencia de `sub-XX_label-lesion_mask.nii.gz`.
- `.bidsignore`, registro ANTs a MNI152, checkpoint de revisión visual.
- fmriprep, rama CON/SIN lesión.

### Qué automatiza `run_feat_dicomize.py` (paso 9 de la guía)
- `fmri-confounds-extract` por tarea (vía `conda run -n <env>`, sin
  necesidad de activar/desactivar el entorno a mano).
- Edición automática del `.fsf` con las EVs de confusión + checkpoint
  antes de correr FEAT.
- QC automático (movimiento, errores de log) + checkpoint humano
  obligatorio de prestats/postats.
- `nifti2dicom` por tarea validada, con alerta si el paciente tiene
  lesión (posible incompatibilidad de espacio MNI entre anatómica y
  funcional — ver limitaciones abajo).

## Estructura del repo

```
fmri_quick_fsl/
├── configure.py
├── pipeline.config.example.ini      # versionado
├── pipeline.config.ini              # generado localmente, NO versionado
├── .gitignore
├── README.md
├── common/
│   └── config.py                    # loader compartido
├── quick_qc/
│   ├── quick_feat.py
│   └── open_mango.py
├── full_pipeline/
│   ├── run_bids_fmriprep.py
│   ├── run_feat_dicomize.py
│   └── templates/
│       └── run_ants_lesion.sh
└── templates/                       # NO versionado, cada quien lo llena
    ├── 100x20.fsf
    └── 150x30.fsf
```

## Estructura de datos esperada (no versionada)

```
funcionales/
└── ApellidoNombrePaciente/
    ├── dicom/sub-01/
    ├── quick_output/nifti/          # quick_qc
    ├── bids_output/                 # full_pipeline
    ├── fmriprep_output/
    ├── anat_derivatives/
    ├── derivatives/confounds/
    ├── work/
    └── out_dicom/
```

## Requisitos

- WSL (Ubuntu) con FSL (`feat`, `fslval`), `dcm2niix`, `wslview` en el PATH
- Docker Desktop en Windows con integración WSL activa
- Servidor X11 en Windows (VcXsrv o similar) para la GUI de bidsmapper
- Python 3.10+, con `nibabel` instalado (`pip install nibabel --break-system-packages`)
- Entorno conda con `fmri-confounds-extract` y `nifti2dicom`
- Licencia de FreeSurfer

## Limitaciones y supuestos sin verificar

- **No automatizado a propósito:** edición del YAML de bidsmapper,
  segmentación de lesión en fsleyes, revisión de postats, importación a
  syngo.via/exportación a AGFA — todo esto requiere juicio clínico o son
  aplicaciones propietarias sin API.
- **Sufijo de entrada a FEAT en `full_pipeline`:** se tomó del ejemplo real
  en el pie de página de la guía (`_desc-preproc_bold.nii.gz`), que es
  inconsistente con la prosa de la sección 9 del mismo documento. Verifica
  el nombre real de tus archivos.
- **Espacio MNI en pacientes con lesión:** fmriprep usa el registro ANTs
  (MNI152NLin2009cAsym) para la anatómica pero produce las funcionales en
  MNI152NLin6Asym. `run_feat_dicomize.py` te avisa de esto antes de
  dicomizar, no lo resuelve automáticamente.
- **`100x20.fsf` / `150x30.fsf` no fueron provistos** al construir estos
  scripts — el parcheo de parámetros usa claves estándar de FSL pero nunca
  se probó contra tus plantillas reales.
- Ningún script de `full_pipeline/` fue ejecutado contra Docker/FSL/conda
  reales durante su construcción — solo se validó sintaxis y la lógica de
  edición de `.fsf` con archivos simulados.

## Flujo de trabajo (Git)

- `main` siempre desplegable
- Ramas por tarea (no por persona), eliminadas tras el merge
- PRs revisados antes de mergear
- Tag antes de cambios grandes (ej. `antes-de-automatizacion-completa`)
