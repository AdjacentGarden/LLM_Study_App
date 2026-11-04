# Independent textbook figures beside the existing Paddle text OCR

This branch retains the existing `paddle-gpu` resident text recognizer. It does
not install PaddleX/PaddlePaddle into that Torch runtime, replace the OCR service,
or change DeepSeek / PUCoding model routes. `FIGURE_LAYOUT_PYTHON` points to a
separate CPU-only interpreter and `FIGURE_LAYOUT_MODEL_DIR` to local immutable
model files. No runtime download or remote inference is performed.

Verified sample package set: Python with onnxruntime1.19.2, opencv-python-headless4.12.0.88,
numpy2.0.2, PyMuPDF1.26.5 and Pillow11.3.0. Reproduce an isolated environment on
an approved host, never in the existing production OCR environment. Configure:

```
FIGURE_LAYOUT_PYTHON=/isolated/layout-venv/bin/python
FIGURE_LAYOUT_MODEL_DIR=/isolated/models/PP-DocLayout_plus-L
FIGURE_LAYOUT_TIMEOUT_SECONDS=3600
```

Use official `PaddlePaddle/PP-DocLayout_plus-L_onnx` revision
`feb74619326f634e0e883218598096a3733ad9f7`; stage `inference.onnx` and `inference.yml`.
The code rejects files whose SHA256 is not:

- model: `77afb2caa74dd13240d087d2eced91d7fcd2caebd16006a0a66162fc8707ff0e`
- config: `d60f782a16f96afb27e8280399899a94c3e9ffc694ffb2f913ea00af1c522f1e`

Official implementation references:
https://github.com/PaddlePaddle/PaddleX/blob/release/3.1/paddlex/inference/models/object_detection/predictor.py
https://github.com/PaddlePaddle/PaddleX/blob/release/3.1/paddlex/inference/models/object_detection/processors.py

Inference is RGB, forced800x800 OpenCV cubic, float32/255, NCHW with actual
height/width scale factors; output coordinates are already original render pixels.
Threshold remains0.5. Only image/chart candidates are considered, then the approved
normalized geometry/caption/heading filter runs BEFORE any crop is written.
The figure detector can miss images; no lowering of confidence threshold or new
visual model call attempts to recover missing candidates. Composites are kept as
detected outer figures rather than divided. Tables/formulas and nearly-whole-page
regions are excluded. Whole-page renders remain in memory, never image files.

`ocr/normalized/figure_manifest.json` records completed/partial/unavailable with
source/model/filter identity; `figure-extraction-audit.json` retains reason codes.
Only independent PNGs are stored under `figures/`. Per-image failures are skipped.
Whole-book dependency/model failures do not fail text OCR or shared publication.
A missing historical manifest is reported internally as not_extracted; neither an
unexecuted nor failed detector is falsely recorded as successful empty extraction.
The product UI does not need an extra extraction warning.

Shared materials publish only after explicit new-flow group intent, valid PDF,
complete normalized OCR/structure and source-matched public diagnostic bank.
The publication allowlist excludes learner preferences, personalized lessons,
notes, answers and progress. Each version/file has SHA256 and byte count;
joining a group is a read-only download of all books, not a cloud workspace creation.
Apply migration15 before starting a PostgreSQL-backed release. No production
installation, model download, service restart or deployment was performed by
adding these source changes.
