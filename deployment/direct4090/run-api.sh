#!/bin/bash
set -euo pipefail
umask 077
export RUNTIME_ROOT="${CLOUDPATH_RUNTIME_ROOT:-/root/autodl-tmp/adaptive-book-ocr}"
set -a
source "$RUNTIME_ROOT/secrets/backend.env"
source "$RUNTIME_ROOT/ops/current-release.env"
set +a
[[ $(cat "$APP_ROOT/RELEASE_COMMIT") == "$CLOUDPATH_RELEASE_SHA" ]] || { echo "Release identity mismatch" >&2; exit 1; }
export PYTHONPATH="$APP_ROOT/backend/src"
export ENV_FILE="$RUNTIME_ROOT/secrets/backend.env"
export CLOUDPATH_LAUNCHER_CONFIG_LOADED=1
export LD_LIBRARY_PATH=/root/miniconda3/lib/python3.12/site-packages/nvidia/cublas/lib:/root/miniconda3/lib/python3.12/site-packages/nvidia/cudnn/lib:/root/miniconda3/lib/python3.12/site-packages/nvidia/cuda_runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
mkdir -p "$RUNTIME_ROOT/run" "$RUNTIME_ROOT/logs"
printf '%s\n' "$$" > "$RUNTIME_ROOT/run/backend.pid"
exec /bin/bash "$APP_ROOT/deployment/server/run_backend.sh" serve
