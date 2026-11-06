#!/bin/bash
set -euo pipefail
umask 077
export RUNTIME_ROOT="${CLOUDPATH_RUNTIME_ROOT:-/root/autodl-tmp/adaptive-book-ocr}"
set -a
source "$RUNTIME_ROOT/secrets/backend.env"
source "$RUNTIME_ROOT/ops/current-release.env"
set +a
export PYTHONPATH="$APP_ROOT/backend/src"
export LD_LIBRARY_PATH=/root/miniconda3/lib/python3.12/site-packages/nvidia/cublas/lib:/root/miniconda3/lib/python3.12/site-packages/nvidia/cudnn/lib:/root/miniconda3/lib/python3.12/site-packages/nvidia/cuda_runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
exec /root/autodl-tmp/adaptive-book-ocr/layout-venv/bin/python "$APP_ROOT/backend/src/adaptive_learning/ingestion/figure_layout.py" --serve --host 127.0.0.1 --port 18202
