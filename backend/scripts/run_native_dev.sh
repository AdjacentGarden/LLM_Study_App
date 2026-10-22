#!/usr/bin/env bash
set -euo pipefail
CLOUDPATH_BACKEND_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$CLOUDPATH_BACKEND_ROOT"
if [[ ! -x .venv/bin/python ]]; then
  echo '先用 Python 3.11+ 创建 backend/.venv，并安装 pip install -e ".[dev,pdf]"；模型接入时再安装 rag 依赖。' >&2
  exit 1
fi
# Default account works without SMTP or OCR. A .env may optionally configure model services.
export NATIVE_LOCAL_LOGIN_ENABLED="${NATIVE_LOCAL_LOGIN_ENABLED:-1}"
options=()
if [[ -f .env ]]; then options+=(--env-file .env); fi
exec .venv/bin/python -m uvicorn adaptive_learning.api.app:app \
  ${options[@]+"${options[@]}"} --host "${CLOUDPATH_HOST:-127.0.0.1}" --port "${CLOUDPATH_PORT:-8100}"
