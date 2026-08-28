# fmri_quick_fsl

Herramientas de línea de comandos para el **QC rápido en sitio** de activaciones fMRI (Paso 2 del protocolo del equipo) — corridas desde la terminal mientras el paciente sigue en el resonador.

## Qué hay aquí

| Script | Qué hace |
|---|---|
| `src/quick_feat.py` | Crea la estructura de carpetas del paciente, espera la llegada de las DICOM, convierte a NIfTI, detecta las tareas funcionales, asigna el diseño `.fsf` correcto y corre FEAT — todo en un solo comando interactivo. |
| `src/open_mango.py` | Abre las imágenes de salida (anatómica + mapa de activación) en el visor Mango para revisión rápida. |

## Requisitos

- FSL (`fslval`, `feat`) en el PATH
- `dcm2niix` en el PATH
- Python 3.10+
- Carpeta `templates/` con los diseños `.fsf` reales del protocolo (`100x20.fsf`, `150x30.fsf`) — **no se suben al repo**, cada quien los coloca localmente

## Uso rápido

```bash
python3 src/quick_feat.py --patient "ApellidoNombrePaciente"
```

Sigue el flujo en pantalla: confirmación de carpetas → espera de DICOM → conversión → revisión de asignación de template → FEAT.

```bash
python3 src/open_mango.py --feat-dir <ruta al .feat generado>
```

## Nota importante sobre los templates `.fsf`

Antes de usar un `.fsf`, verificar que su `fmri(npts)` interno coincida realmente con el número de volúmenes esperado — el nombre del archivo (`150x30.fsf`) no garantiza que el valor interno sea correcto. `quick_feat.py` compara esto automáticamente contra el NIfTI real y avisa si no coinciden, en vez de asignar en silencio.

## Estructura de datos esperada (no versionada)

```
funcionales/
└── ApellidoNombrePaciente/
    ├── dicom/sub-01/          ← DICOM crudos
    └── quick_output/
        ├── nifti/             ← salida de dcm2niix
        └── *.feat/            ← salidas de FEAT
```

Esta carpeta `funcionales/` contiene datos de pacientes y queda excluida del repo vía `.gitignore` — nunca debe subirse a GitHub.

## Flujo de trabajo (Git)

- `main` siempre desplegable
- Ramas por tarea (no por persona), eliminadas tras el merge
- PRs revisados antes de mergear
