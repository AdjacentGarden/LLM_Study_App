#!/usr/bin/env bash
# Starts a disposable loopback backend with explicit fixture OCR/model adapters.
set -euo pipefail
CLOUDPATH_IOS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLOUDPATH_BACKEND_ROOT="${CLOUDPATH_BACKEND_ROOT:-$(cd "$CLOUDPATH_IOS_ROOT/../backend" && pwd)}"
if [[ ! -x "$CLOUDPATH_BACKEND_ROOT/.venv/bin/python" ]]; then
  echo '需要先安装 backend 的 dev,pdf 依赖到 backend/.venv。' >&2
  exit 1
fi
if lsof -ti tcp:18101 >/dev/null; then
  echo '18101 端口已占用，请先结束之前的测试服务。' >&2
  exit 1
fi
mkdir -p "$CLOUDPATH_IOS_ROOT/output"
CLOUDPATH_TEST_LOG="$(mktemp "$CLOUDPATH_IOS_ROOT/output/cloud-test-server.XXXXXX")"
(cd "$CLOUDPATH_BACKEND_ROOT" && NATIVE_E2E_TEST_SERVER=1 exec .venv/bin/python tests/run_native_server.py) >"$CLOUDPATH_TEST_LOG" 2>&1 &
CLOUDPATH_TEST_PID=$!
trap 'kill "$CLOUDPATH_TEST_PID" 2>/dev/null || true; wait "$CLOUDPATH_TEST_PID" 2>/dev/null || true' EXIT
"$CLOUDPATH_BACKEND_ROOT/.venv/bin/python" - <<'PY'
import json,time,urllib.request
for _ in range(60):
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:18101/api/native-test/health',timeout=1) as response:
            assert json.load(response)['fixture_adapters']
        break
    except Exception:
        time.sleep(.25)
else:
    raise SystemExit('测试后端未能启动，请检查 output/cloud-test-server.* 日志')
PY
"$CLOUDPATH_IOS_ROOT/scripts/test.sh" "$@"
