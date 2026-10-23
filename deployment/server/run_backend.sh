#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/data1/zhenghang/adaptive-book-ocr/app}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/data1/zhenghang/adaptive-book-ocr}"
SECRET_FILE="${SECRET_FILE:-${RUNTIME_ROOT}/secrets/adaptive-book.env}"
PID_FILE="${RUNTIME_ROOT}/run/backend.pid"
LOG_FILE="${RUNTIME_ROOT}/logs/backend.log"

is_our_process() {
  local pid="$1"
  [[ -r "/proc/${pid}/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -Fq "adaptive_learning.api.app:app"
}

stop_backend() {
  if [[ ! -s "${PID_FILE}" ]]; then
    echo "backend is not running (no pid file)"
    return 0
  fi

  local pid
  pid="$(<"${PID_FILE}")"
  if [[ ! "${pid}" =~ ^[0-9]+$ ]] || ! is_our_process "${pid}"; then
    echo "refusing to stop unverified process from ${PID_FILE}: ${pid}" >&2
    return 1
  fi

  kill -TERM -- "-${pid}" 2>/dev/null || kill -TERM "${pid}"
  for _ in {1..300}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      rm -f "${PID_FILE}"
      echo "backend stopped"
      return 0
    fi
    sleep 0.1
  done

  echo "backend did not stop gracefully; terminating its verified process group" >&2
  kill -KILL -- "-${pid}" 2>/dev/null || kill -KILL "${pid}"
  rm -f "${PID_FILE}"
}

start_backend() {
  mkdir -p "${RUNTIME_ROOT}/data" "${RUNTIME_ROOT}/logs" "${RUNTIME_ROOT}/run"
  if [[ -s "${PID_FILE}" ]]; then
    local current_pid
    current_pid="$(<"${PID_FILE}")"
    if [[ "${current_pid}" =~ ^[0-9]+$ ]] && is_our_process "${current_pid}"; then
      echo "backend is already running: ${current_pid}"
      return 0
    fi
    rm -f "${PID_FILE}"
  fi

  if command -v ss >/dev/null && ss -ltn | grep -q ":${APP_PORT:-8100} "; then
    echo "refusing to start: port ${APP_PORT:-8100} is already in use" >&2
    return 1
  fi

  setsid "$0" serve >>"${LOG_FILE}" 2>&1 < /dev/null &
  local pid=$!
  echo "${pid}" > "${PID_FILE}"
  # GPU RAG warm-up can exceed ten seconds after a cold restart.
  for _ in {1..300}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      echo "backend exited during startup; inspect ${LOG_FILE}" >&2
      rm -f "${PID_FILE}"
      return 1
    fi
    if curl -fs --max-time 1 "http://127.0.0.1:${APP_PORT:-8100}/api/health" >/dev/null; then
      echo "backend started: ${pid}"
      return 0
    fi
    sleep 0.1
  done

  echo "backend did not become healthy; inspect ${LOG_FILE}" >&2
  return 1
}

case "${1:-serve}" in
  start)
    start_backend
    exit $?
    ;;
  stop)
    stop_backend
    exit $?
    ;;
  restart)
    stop_backend
    start_backend
    exit $?
    ;;
  status)
    if [[ -s "${PID_FILE}" ]] && is_our_process "$(<"${PID_FILE}")"; then
      echo "backend is running: $(<"${PID_FILE}")"
      exit 0
    fi
    echo "backend is stopped"
    exit 1
    ;;
  serve)
    ;;
  *)
    echo "usage: $0 [start|stop|restart|status|serve]" >&2
    exit 2
    ;;
esac

if [[ ! -r "${SECRET_FILE}" ]]; then
  echo "backend secret file is missing or unreadable" >&2
  exit 1
fi

set -a
source "${SECRET_FILE}"
if [[ -r "${RUNTIME_ROOT}/config/published-books.env" ]]; then
  source "${RUNTIME_ROOT}/config/published-books.env"
fi
if [[ -r "${RUNTIME_ROOT}/config/accounts.env" ]]; then
  source "${RUNTIME_ROOT}/config/accounts.env"
fi
if [[ -r "${RUNTIME_ROOT}/secrets/learning-studio.env" ]]; then
  source "${RUNTIME_ROOT}/secrets/learning-studio.env"
fi
if [[ -r "${RUNTIME_ROOT}/config/gpu.env" ]]; then
  source "${RUNTIME_ROOT}/config/gpu.env"
fi
if [[ -r "${RUNTIME_ROOT}/config/ingestion-performance.env" ]]; then
  source "${RUNTIME_ROOT}/config/ingestion-performance.env"
fi
# Release-owned, non-secret routing overrides win after legacy runtime profiles.
# The default checkout remains unchanged; versioned service units set this path.
if [[ -n "${RELEASE_ENV_FILE:-}" ]]; then
  if [[ ! -r "${RELEASE_ENV_FILE}" ]]; then
    echo "release environment override file is missing or unreadable" >&2
    exit 1
  fi
  source "${RELEASE_ENV_FILE}"
fi
set +a

export APP_ENV="production"
export APP_HOST="0.0.0.0"
export APP_PORT="${APP_PORT:-8100}"
export APP_DATA_DIR="${RUNTIME_ROOT}/data"
export OCR_WORKER_ENABLED="${OCR_WORKER_ENABLED:-true}"
export OCR_WORKER_LEASE_SECONDS="${OCR_WORKER_LEASE_SECONDS:-90}"
export OCR_WORKER_POLL_SECONDS="${OCR_WORKER_POLL_SECONDS:-1}"
export OCR_RETRY_DELAY_SECONDS="${OCR_RETRY_DELAY_SECONDS:-30}"
export OCR_MAX_ATTEMPTS="${OCR_MAX_ATTEMPTS:-3}"
export OCR_BACKEND="${OCR_BACKEND:-vlm-auto-engine}"
export MINERU_TOOLS_CONFIG_JSON="${MINERU_TOOLS_CONFIG_JSON:-${RUNTIME_ROOT}/config/mineru.json}"
export MINERU_MODEL_SOURCE="${MINERU_MODEL_SOURCE:-local}"
export RAG_BOOK_ID="biology-required-2"
export PUBLISHED_BOOK_IDS="${PUBLISHED_BOOK_IDS:-biology-required-2}"
export RAG_INDEX_DIR="${RUNTIME_ROOT}/rag-eval/index"
export RAG_EMBEDDING_MODEL_PATH="${RUNTIME_ROOT}/rag-eval/models/bge-small-zh-v1.5"
export RAG_RERANKER_MODEL_PATH="${RUNTIME_ROOT}/rag-eval/models/bge-reranker-base"
export RAG_DEVICE="${RAG_DEVICE:-auto}"
export RAG_REFUSAL_SCORE_THRESHOLD="0"
export RAG_TOP_PAGES="5"
export RAG_MAX_EVIDENCE="10"
export VOICE_ASR_PYTHON="${VOICE_ASR_PYTHON:-/usr/bin/python3}"
export VOICE_ASR_PYTHONPATH="${VOICE_ASR_PYTHONPATH:-${RUNTIME_ROOT}/studio-runtime/asr-lib}"
export VOICE_ASR_MODEL="${VOICE_ASR_MODEL:-${RUNTIME_ROOT}/models/faster-whisper-small}"
export VOICE_ASR_SCRIPT="${VOICE_ASR_SCRIPT:-${APP_ROOT}/deployment/server/transcribe_voice_note.py}"
export VOICE_ASR_DEVICE="${VOICE_ASR_DEVICE:-cpu}"
export VOICE_ASR_COMPUTE_TYPE="${VOICE_ASR_COMPUTE_TYPE:-int8}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-1}"
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"
export PYTHONPATH="${APP_ROOT}/backend/src:${RUNTIME_ROOT}/runtime-patches/lib/python3.12/site-packages:${RUNTIME_ROOT}/runtime/lib/python3.12/site-packages"

# Direct model access works on this server. Do not depend on a laptop reverse tunnel.
# Operators can still explicitly configure a proxy in the server environment.
export LLM_HTTPS_PROXY="${LLM_HTTPS_PROXY:-}"
if [[ -n "${LLM_HTTPS_PROXY}" ]]; then
  export HTTPS_PROXY="${LLM_HTTPS_PROXY}"
  export HTTP_PROXY="${LLM_HTTPS_PROXY}"
fi

mkdir -p "${RUNTIME_ROOT}/data" "${RUNTIME_ROOT}/logs" "${RUNTIME_ROOT}/run"
cd "${APP_ROOT}"

# Keep enough headroom for concurrent uploads, model sockets and SQLite WAL files.
# Repositories still close every connection explicitly; this is only a production guardrail.
ulimit -n 65536 2>/dev/null || true

# Keep the GPU OCR model loaded between jobs.
if [[ "${OCR_BACKEND}" == "paddle-gpu" ]]; then
  export FAST_OCR_API_URL="${FAST_OCR_API_URL:-http://127.0.0.1:${FAST_OCR_PORT:-18201}}"
  if [[ "${FAST_OCR_API_URL}" != "http://127.0.0.1:${FAST_OCR_PORT:-18201}" ]]; then
    echo "FAST_OCR_API_URL must match the managed loopback service port" >&2
    exit 1
  fi
  # A versioned backend can reuse the stable runtime OCR manager only when it
  # matches the reviewed copy;
  # its PID verifier intentionally rejects managers launched from other paths.
  fast_ocr_manager="${FAST_OCR_MANAGER_SCRIPT:-${APP_ROOT}/deployment/server/fast_ocr_service.py}"
  if [[ ! -f "${fast_ocr_manager}" || ! -r "${fast_ocr_manager}" ]]; then
    echo "fast OCR manager is missing or unreadable" >&2
    exit 1
  fi
  if [[ -n "${FAST_OCR_MANAGER_SHA256:-}" ]]; then
    /usr/bin/python3 - "${fast_ocr_manager}" "${FAST_OCR_MANAGER_SHA256}" <<'PY'
import hashlib
from pathlib import Path
import sys
if hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest() != sys.argv[2]:
    raise SystemExit("fast OCR manager differs from the reviewed release")
PY
  fi
  RUNTIME_ROOT="${RUNTIME_ROOT}" APP_ROOT="${APP_ROOT}" \
    /usr/bin/python3 "${fast_ocr_manager}" start
elif [[ "${MINERU_API_URL:-}" == "http://127.0.0.1:${OCR_RESIDENT_PORT:-18200}" ]]; then
  /usr/bin/python3 "${APP_ROOT}/deployment/server/ocr_resident_service.py" start
fi

exec /home/zhenghang/download/enter/bin/python -m uvicorn \
  adaptive_learning.api.app:app \
  --host "${APP_HOST}" \
  --port "${APP_PORT}" \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips="*"
