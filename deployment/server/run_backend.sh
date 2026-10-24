#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT_DIR}/run_backend.sh"
export APP_ROOT="${APP_ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
export RUNTIME_ROOT="${RUNTIME_ROOT:-${APP_ROOT}/data/ocr-runtime}"

# Load configuration before both start's health probe and serve's interpreter.
# The detached child inherits these exact values, including APP_PORT.
if [[ "${CLOUDPATH_LAUNCHER_CONFIG_LOADED:-0}" != 1 ]]; then
  env_file_required="${ENV_FILE:-${SECRET_FILE:-}}"
  default_env_file="${APP_ROOT}/backend/.env"
  if [[ ! -r "${default_env_file}" && -r "${RUNTIME_ROOT}/secrets/adaptive-book.env" ]]; then
    default_env_file="${RUNTIME_ROOT}/secrets/adaptive-book.env"
  fi
  ENV_FILE="${ENV_FILE:-${SECRET_FILE:-${default_env_file}}}"
  if [[ -n "${env_file_required}" ]]; then
    [[ -r "${ENV_FILE}" ]] || { echo "environment file is unreadable: ${ENV_FILE}" >&2; exit 1; }
  fi
  set -a
  [[ ! -r "${ENV_FILE}" ]] || source "${ENV_FILE}"
  for profile in \
    "${RUNTIME_ROOT}/config/published-books.env" \
    "${RUNTIME_ROOT}/config/accounts.env" \
    "${RUNTIME_ROOT}/secrets/learning-studio.env" \
    "${GPU_PROFILE_FILE:-${RUNTIME_ROOT}/config/gpu.env}" \
    "${INGESTION_PROFILE_FILE:-${RUNTIME_ROOT}/config/ingestion-performance.env}"; do
    [[ ! -r "${profile}" ]] || source "${profile}"
  done
  if [[ -n "${RELEASE_ENV_FILE:-}" ]]; then
    [[ -r "${RELEASE_ENV_FILE}" ]] || { echo "release environment override file is unreadable" >&2; exit 1; }
    source "${RELEASE_ENV_FILE}"
  fi
  set +a
  export CLOUDPATH_LAUNCHER_CONFIG_LOADED=1
fi

export APP_ROOT RUNTIME_ROOT
export APP_PYTHON="${APP_PYTHON:-${APP_ROOT}/backend/.venv/bin/python}"
export OCR_PYTHON="${OCR_PYTHON:-${APP_PYTHON}}"
export APP_ENV="${APP_ENV:-production}"
export APP_HOST="${APP_HOST:-127.0.0.1}"
export APP_PORT="${APP_PORT:-8100}"
export BACKEND_STARTUP_TIMEOUT_SECONDS="${BACKEND_STARTUP_TIMEOUT_SECONDS:-300}"
export APP_DATA_DIR="${APP_DATA_DIR:-${RUNTIME_ROOT}/data}"
[[ "${APP_DATA_DIR}" = /* ]] || export APP_DATA_DIR="${APP_ROOT}/${APP_DATA_DIR}"
export FAST_OCR_SOURCE_ROOTS="${FAST_OCR_SOURCE_ROOTS:-${RUNTIME_ROOT}/input:${APP_DATA_DIR}:${RUNTIME_ROOT}/benchmarks}"
export FAST_OCR_OUTPUT_ROOTS="${FAST_OCR_OUTPUT_ROOTS:-${APP_DATA_DIR}:${RUNTIME_ROOT}/benchmarks}"
export FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
export OCR_WORKER_ENABLED="${OCR_WORKER_ENABLED:-true}"
export OCR_BACKEND="${OCR_BACKEND:-vlm-auto-engine}"
export VOICE_ASR_PYTHON="${VOICE_ASR_PYTHON:-${APP_PYTHON}}"
export VOICE_ASR_PYTHONPATH="${VOICE_ASR_PYTHONPATH:-${RUNTIME_ROOT}/studio-runtime/asr-lib}"
export VOICE_ASR_MODEL="${VOICE_ASR_MODEL:-${RUNTIME_ROOT}/models/faster-whisper-small}"
export VOICE_ASR_SCRIPT="${VOICE_ASR_SCRIPT:-${APP_ROOT}/deployment/server/transcribe_voice_note.py}"
export VOICE_ASR_DEVICE="${VOICE_ASR_DEVICE:-cpu}"
export VOICE_ASR_COMPUTE_TYPE="${VOICE_ASR_COMPUTE_TYPE:-int8}"
case ":${PYTHONPATH:-}:" in
  *":${APP_ROOT}/backend/src:"*) ;;
  *) export PYTHONPATH="${APP_ROOT}/backend/src${PYTHONPATH:+:${PYTHONPATH}}" ;;
esac

if [[ -n "${LLM_HTTPS_PROXY:-}" ]]; then
  export HTTPS_PROXY="${LLM_HTTPS_PROXY}"
  export HTTP_PROXY="${LLM_HTTPS_PROXY}"
fi

PID_FILE="${RUNTIME_ROOT}/run/backend.pid"
LOG_FILE="${RUNTIME_ROOT}/logs/backend.log"

is_our_process() {
  local pid="$1" command
  [[ "${pid}" =~ ^[0-9]+$ && "${pid}" -gt 1 ]] || return 1
  if [[ -r "/proc/${pid}/cmdline" ]]; then
    command="$(tr '\0' ' ' < "/proc/${pid}/cmdline")"
  else
    command="$(ps -p "${pid}" -o command= 2>/dev/null)" || return 1
  fi
  [[ "${command}" == *"adaptive_learning.api.app:app"* || "${command}" == *"${SCRIPT} serve"* ]]
}

stop_backend() {
  if [[ ! -s "${PID_FILE}" ]]; then
    echo "backend is not running (no pid file)"
    return
  fi
  local pid
  pid="$(<"${PID_FILE}")"
  if ! is_our_process "${pid}"; then
    # A reboot or crash can leave a PID file after its process has disappeared.
    # Check both signal permission and the process table so an inaccessible live
    # process is never mistaken for a stale PID.
    if [[ "${pid}" =~ ^[0-9]+$ && "${pid}" -gt 1 ]] \
        && ! kill -0 "${pid}" 2>/dev/null \
        && ! ps -p "${pid}" -o pid= >/dev/null 2>&1; then
      rm -f "${PID_FILE}"
      echo "backend is not running (removed stale pid file)"
      return
    fi
    echo "refusing to stop unverified process from ${PID_FILE}: ${pid}" >&2
    return 1
  fi
  kill -TERM -- "-${pid}" 2>/dev/null || kill -TERM "${pid}"
  for _ in {1..300}; do
    if ! is_our_process "${pid}"; then
      rm -f "${PID_FILE}"
      echo "backend stopped"
      return
    fi
    sleep 0.1
  done
  kill -KILL -- "-${pid}" 2>/dev/null || kill -KILL "${pid}"
  rm -f "${PID_FILE}"
  echo "backend stopped (forced)"
}

start_backend() {
  mkdir -p "${APP_DATA_DIR}" "${RUNTIME_ROOT}/logs" "${RUNTIME_ROOT}/run"
  if [[ -s "${PID_FILE}" ]] && is_our_process "$(<"${PID_FILE}")"; then
    echo "backend is already running: $(<"${PID_FILE}")"
    return
  fi
  "${APP_PYTHON}" -c 'import socket,sys
with socket.socket() as probe:
    probe.settimeout(1)
    if probe.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0:
        raise SystemExit("backend port is already in use")' "${APP_PORT}"
  local pid deadline
  deadline="$("${APP_PYTHON}" -c 'import math,sys,time
seconds=float(sys.argv[1])
if not math.isfinite(seconds) or seconds <= 0:
    raise SystemExit("BACKEND_STARTUP_TIMEOUT_SECONDS must be positive and finite")
print(time.monotonic() + seconds)' "${BACKEND_STARTUP_TIMEOUT_SECONDS}")"
  pid="$("${APP_PYTHON}" -c 'import subprocess,sys
with open(sys.argv[2], "a") as log:
    child=subprocess.Popen([sys.argv[1], "serve"], stdin=subprocess.DEVNULL,
                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
print(child.pid)' "${SCRIPT}" "${LOG_FILE}")"
  echo "${pid}" > "${PID_FILE}"
  while before_deadline "${deadline}"; do
    if ! is_our_process "${pid}"; then
      echo "backend exited during startup; inspect ${LOG_FILE}" >&2
      rm -f "${PID_FILE}"
      return 1
    fi
    if curl --noproxy '*' -fs --max-time 1 "http://127.0.0.1:${APP_PORT}/api/health" >/dev/null \
        && before_deadline "${deadline}"; then
      echo "backend started: ${pid}"
      return
    fi
    sleep 0.1
  done
  echo "backend did not become healthy; inspect ${LOG_FILE}" >&2
  return 1
}

before_deadline() {
  "${APP_PYTHON}" -c 'import sys,time; sys.exit(time.monotonic() >= float(sys.argv[1]))' "$1"
}

# Managed services bind IPv4 loopback. An externally managed loopback endpoint
# can instead be selected with FAST_OCR_MANAGED=false / no resident enable flag.
managed_port() {
  "${APP_PYTHON}" -c 'import sys; from urllib.parse import urlsplit
value, explicit, fallback = sys.argv[1:]
if value:
    parsed=urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost","127.0.0.1"}
        or parsed.username or parsed.password or parsed.path not in {"","/"}
        or parsed.query or parsed.fragment):
        raise SystemExit("managed OCR URL must be an IPv4 loopback HTTP origin")
    port=parsed.port or 80
    if explicit and int(explicit) != port:
        raise SystemExit("managed OCR URL and port disagree")
else:
    port=int(explicit or fallback)
if not 1 <= port <= 65535:
    raise SystemExit("OCR port must be between 1 and 65535")
print(port)' "$1" "$2" "$3"
}

is_true() {
  case "$1" in
    1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Oo][Nn]) return 0 ;;
    *) return 1 ;;
  esac
}

case "${1:-serve}" in
  start) start_backend; exit $? ;;
  stop) stop_backend; exit $? ;;
  restart) stop_backend; start_backend; exit $? ;;
  status)
    if [[ -s "${PID_FILE}" ]] && is_our_process "$(<"${PID_FILE}")"; then
      echo "backend is running: $(<"${PID_FILE}")"; exit 0
    fi
    echo "backend is stopped"; exit 1 ;;
  serve) ;;
  *) echo "usage: $0 [start|stop|restart|status|serve]" >&2; exit 2 ;;
esac

if is_true "${OCR_WORKER_ENABLED}"; then
  if [[ "${OCR_BACKEND}" == paddle-gpu ]]; then
    if is_true "${FAST_OCR_MANAGED:-true}"; then
      FAST_OCR_PORT="$(managed_port "${FAST_OCR_API_URL:-}" "${FAST_OCR_PORT:-}" 18201)"
      export FAST_OCR_PORT
      export FAST_OCR_API_URL="${FAST_OCR_API_URL:-http://127.0.0.1:${FAST_OCR_PORT}}"
      fast_ocr_manager="${FAST_OCR_MANAGER_SCRIPT:-${SCRIPT_DIR}/fast_ocr_service.py}"
      [[ -r "${fast_ocr_manager}" ]] || { echo "fast OCR manager is unreadable" >&2; exit 1; }
      if [[ -n "${FAST_OCR_MANAGER_SHA256:-}" ]]; then
        "${APP_PYTHON}" -c 'import hashlib,sys
from pathlib import Path
if hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest() != sys.argv[2]:
    raise SystemExit("fast OCR manager differs from the reviewed release")' \
          "${fast_ocr_manager}" "${FAST_OCR_MANAGER_SHA256}"
      fi
      "${OCR_PYTHON}" "${fast_ocr_manager}" start --port "${FAST_OCR_PORT}"
    fi
  elif is_true "${MINERU_RESIDENT_ENABLED:-false}"; then
    OCR_RESIDENT_PORT="$(managed_port "${MINERU_API_URL:-}" "${OCR_RESIDENT_PORT:-}" 18200)"
    export OCR_RESIDENT_PORT
    export MINERU_API_URL="${MINERU_API_URL:-http://127.0.0.1:${OCR_RESIDENT_PORT}}"
    "${OCR_PYTHON}" "${SCRIPT_DIR}/ocr_resident_service.py" start --port "${OCR_RESIDENT_PORT}"
  fi
fi

mkdir -p "${APP_DATA_DIR}" "${RUNTIME_ROOT}/logs" "${RUNTIME_ROOT}/run"
cd "${APP_ROOT}"
exec "${APP_PYTHON}" -m uvicorn adaptive_learning.api.app:app \
  --host "${APP_HOST}" --port "${APP_PORT}" --workers 1 \
  --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS}"
