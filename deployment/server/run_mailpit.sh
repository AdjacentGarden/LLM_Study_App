#!/usr/bin/env bash
set -euo pipefail

RUNTIME_ROOT="${RUNTIME_ROOT:-/data1/zhenghang/adaptive-book-ocr}"
MAILPIT_BIN="${MAILPIT_BIN:-${RUNTIME_ROOT}/tools/mailpit/current/mailpit}"
PID_FILE="${RUNTIME_ROOT}/run/mailpit.pid"
LOG_FILE="${RUNTIME_ROOT}/logs/mailpit.log"
DATABASE="${RUNTIME_ROOT}/data/state/mailpit.db"

is_our_process() {
  local pid="$1"
  [[ -r "/proc/${pid}/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -Fq "${MAILPIT_BIN}"
}

stop_mailpit() {
  if [[ ! -s "${PID_FILE}" ]]; then
    echo "mailpit is not running (no pid file)"
    return 0
  fi
  local pid
  pid="$(<"${PID_FILE}")"
  if [[ ! "${pid}" =~ ^[0-9]+$ ]] || ! is_our_process "${pid}"; then
    echo "refusing to stop unverified process from ${PID_FILE}: ${pid}" >&2
    return 1
  fi
  kill -TERM "${pid}"
  for _ in {1..100}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      rm -f "${PID_FILE}"
      echo "mailpit stopped"
      return 0
    fi
    sleep 0.1
  done
  echo "mailpit did not stop gracefully" >&2
  return 1
}

start_mailpit() {
  [[ -x "${MAILPIT_BIN}" ]] || {
    echo "mailpit binary is missing or not executable: ${MAILPIT_BIN}" >&2
    return 1
  }
  mkdir -p "${RUNTIME_ROOT}/run" "${RUNTIME_ROOT}/logs" "$(dirname "${DATABASE}")"
  if [[ -s "${PID_FILE}" ]]; then
    local pid
    pid="$(<"${PID_FILE}")"
    if [[ "${pid}" =~ ^[0-9]+$ ]] && is_our_process "${pid}"; then
      echo "mailpit is already running: ${pid}"
      return 0
    fi
    rm -f "${PID_FILE}"
  fi
  setsid "${MAILPIT_BIN}" \
    --database "${DATABASE}" \
    --listen 127.0.0.1:8025 \
    --smtp 127.0.0.1:1025 \
    --max 200 \
    --max-age 7d \
    --max-message-size 1 \
    --label "CloudPath private test mailbox" \
    --disable-version-check \
    --block-remote-css-and-fonts \
    >>"${LOG_FILE}" 2>&1 < /dev/null &
  local pid=$!
  echo "${pid}" > "${PID_FILE}"
  for _ in {1..100}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      echo "mailpit exited during startup; inspect ${LOG_FILE}" >&2
      rm -f "${PID_FILE}"
      return 1
    fi
    if curl -fs --max-time 1 http://127.0.0.1:8025/ >/dev/null; then
      echo "mailpit started: ${pid}"
      return 0
    fi
    sleep 0.1
  done
  echo "mailpit did not become healthy; inspect ${LOG_FILE}" >&2
  return 1
}

case "${1:-status}" in
  start) start_mailpit ;;
  stop) stop_mailpit ;;
  restart) stop_mailpit; start_mailpit ;;
  status)
    if [[ -s "${PID_FILE}" ]] && is_our_process "$(<"${PID_FILE}")"; then
      echo "mailpit is running: $(<"${PID_FILE}")"
    else
      echo "mailpit is stopped"
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 [start|stop|restart|status]" >&2
    exit 2
    ;;
esac
