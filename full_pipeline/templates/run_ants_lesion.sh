#!/bin/bash
# run_ants_lesion.sh — Registro ANTs con cost function masking
# Uso: ./run_ants_lesion.sh /mnt/c/Ruta/Al/Estudio
# -- EDITAR --------------------------------------------------
SUBJ="01"
PROJ="${1}"
TEMPLATEFLOW="${HOME}/.cache/templateflow"
THREADS=8
# ------------------------------------------------------------

ANAT="${PROJ}/bids_output/sub-${SUBJ}/anat"
WORK="${PROJ}/work/sub-${SUBJ}/ants"
DERIV="${PROJ}/anat_derivatives/sub-${SUBJ}/anat"
TMPL_DIR="${TEMPLATEFLOW}/tpl-MNI152NLin2009cAsym"
mkdir -p "${WORK}" "${DERIV}"

# ── VERIFICAR / DESCARGAR TEMPLATEFLOW ───────────────────────────
TMPL_FILE="${TMPL_DIR}/tpl-MNI152NLin2009cAsym_res-01_T1w.nii.gz"
TMPL_MASK_FILE="${TMPL_DIR}/tpl-MNI152NLin2009cAsym_res-01_desc-brain_mask.nii.gz"

if [ ! -f "${TMPL_FILE}" ] || [ ! -f "${TMPL_MASK_FILE}" ]; then
    echo ">>> Templateflow no encontrado. Descargando..."
    docker run --rm \
        -v "${TEMPLATEFLOW}:/templateflow" \
        -e TEMPLATEFLOW_HOME=/templateflow \
        nipreps/fmriprep:latest \
        python -c "
import templateflow.api as tf
tf.get('MNI152NLin2009cAsym', resolution=1, desc=None, suffix='T1w')
tf.get('MNI152NLin2009cAsym', resolution=1, desc='brain', suffix='mask')
print('Descarga completa.')
"
else
    echo ">>> Templateflow OK."
fi
# ─────────────────────────────────────────────────────────────────

# Paths dentro del contenedor
C_ANAT="/data/anat"
C_WORK="/data/work"
C_DERIV="/data/deriv"
C_TMPL="/data/template"

T1_LOCAL=$(find "${ANAT}" -name "*_T1w.nii.gz" | head -1)
T1="${C_ANAT}/$(basename ${T1_LOCAL})"
MASK="${C_ANAT}/sub-${SUBJ}_label-lesion_mask.nii.gz"
TMPL="${C_TMPL}/tpl-MNI152NLin2009cAsym_res-01_T1w.nii.gz"
TMPL_MASK="${C_TMPL}/tpl-MNI152NLin2009cAsym_res-01_desc-brain_mask.nii.gz"
OUT="${C_WORK}/sub-${SUBJ}_to_MNI_"

BM="${C_WORK}/sub-${SUBJ}_BrainExtractionMask.nii.gz"
BMNL="${C_WORK}/sub-${SUBJ}_brain_mask_nolesion.nii.gz"

# Funcion helper
ants() {
    docker run --rm \
        -v "${ANAT}:${C_ANAT}:ro" \
        -v "${WORK}:${C_WORK}" \
        -v "${DERIV}:${C_DERIV}" \
        -v "${TMPL_DIR}:${C_TMPL}:ro" \
        antsx/ants "$@"
}

# ── PASO A: Extraccion de cerebro ─────────────────────────────────
echo ""
echo ">>> PASO A: Extrayendo cerebro..."
ants antsBrainExtraction.sh \
    -d 3 \
    -a "${T1}" \
    -e "${TMPL}" \
    -m "${TMPL_MASK}" \
    -o "${C_WORK}/sub-${SUBJ}_"

# ── PASO B: Mascara sin lesion ────────────────────────────────────
echo ""
echo ">>> PASO B: Generando mascara sin lesion..."
ants ImageMath 3 \
    "${BMNL}" \
    - \
    "${BM}" \
    "${MASK}"

ants ThresholdImage 3 \
    "${BMNL}" \
    "${BMNL}" \
    0.5 1 1 0

# ── PASO C: Registro ANTs SyN con cost function masking ───────────
echo ""
echo ">>> PASO C: Registro ANTs (45-90 min)..."
ants antsRegistration \
    --dimensionality 3 \
    --float 1 \
    --output "[${OUT},${OUT}Warped.nii.gz]" \
    --interpolation LanczosWindowedSinc \
    --use-histogram-matching 0 \
    --winsorize-image-intensities "[0.005,0.995]" \
    --initial-moving-transform "[${TMPL},${T1},1]" \
    --transform "Rigid[0.1]" \
        --metric "MI[${TMPL},${T1},1,32,Regular,0.25]" \
        --masks "[${TMPL_MASK},${BMNL}]" \
        --convergence "[1000x500x250x100,1e-6,10]" \
        --shrink-factors 8x4x2x1 \
        --smoothing-sigmas 3x2x1x0vox \
    --transform "Affine[0.1]" \
        --metric "MI[${TMPL},${T1},1,32,Regular,0.25]" \
        --masks "[${TMPL_MASK},${BMNL}]" \
        --convergence "[1000x500x250x100,1e-6,10]" \
        --shrink-factors 8x4x2x1 \
        --smoothing-sigmas 3x2x1x0vox \
    --transform "SyN[0.1,3,0]" \
        --metric "CC[${TMPL},${T1},1,4]" \
        --masks "[${TMPL_MASK},${BMNL}]" \
        --convergence "[100x70x50x20,1e-6,10]" \
        --shrink-factors 8x4x2x1 \
        --smoothing-sigmas 3x2x1x0vox \
    --verbose 1

# ── PASO D: Convertir transformaciones a .h5 ─────────────────────
echo ""
echo ">>> PASO D: Convirtiendo transformaciones a .h5..."

# nativo -> MNI
ants antsApplyTransforms \
    -d 3 \
    --input "${T1}" \
    --reference-image "${TMPL}" \
    --transform "${OUT}1Warp.nii.gz" \
    --transform "${OUT}0GenericAffine.mat" \
    --output "Linear[${C_DERIV}/sub-${SUBJ}_from-T1w_to-MNI152NLin2009cAsym_mode-image_xfm.h5,1]"

# MNI -> nativo (inversa)
ants antsApplyTransforms \
    -d 3 \
    --input "${TMPL}" \
    --reference-image "${T1}" \
    --transform "[${OUT}0GenericAffine.mat,1]" \
    --transform "${OUT}1InverseWarp.nii.gz" \
    --output "Linear[${C_DERIV}/sub-${SUBJ}_from-MNI152NLin2009cAsym_to-T1w_mode-image_xfm.h5,1]"

# ── PASO E: Organizar anat_derivatives ───────────────────────────
echo ""
echo ">>> PASO E: Organizando anat_derivatives..."

cp "${WORK}/sub-${SUBJ}_to_MNI_Warped.nii.gz" \
   "${DERIV}/sub-${SUBJ}_space-MNI152NLin2009cAsym_desc-preproc_T1w.nii.gz"

cp "${WORK}/sub-${SUBJ}_BrainExtractionMask.nii.gz" \
   "${DERIV}/sub-${SUBJ}_desc-brain_mask.nii.gz"

echo ""
echo "Listo. Verifica visualmente:"
echo "  ${WORK}/sub-${SUBJ}_to_MNI_Warped.nii.gz"
