#!/usr/bin/env bash
# Shared local paths only. Importing fixtures from React is a separate operation.
set -euo pipefail
BOOKCOURSE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOKCOURSE_DERIVED_DATA="${BOOKCOURSE_DERIVED_DATA:-$BOOKCOURSE_ROOT/DerivedData}"
BOOKCOURSE_PACKAGES="${BOOKCOURSE_PACKAGES:-$BOOKCOURSE_ROOT/.build/packages}"
BOOKCOURSE_CONFIGURATION="${BOOKCOURSE_CONFIGURATION:-Debug}"
bookcourse_xcode() {
  command -v xcodebuild >/dev/null || { echo '需要完整 Xcode；请先用 xcode-select 选择 Xcode.app/Contents/Developer。' >&2; return 1; }
}
bookcourse_simulator() {
  # An explicit device is preferred. Otherwise use a booted iOS device, then the first available iPhone.
  xcrun simctl list devices available --json | python3 -c '
import json,sys,re
items=[d for runtime,ds in json.load(sys.stdin)["devices"].items() if (match:=re.search(r"\.iOS-(\d+)",runtime)) and int(match.group(1))>=17 for d in ds if d.get("isAvailable",True)]
booted=[d for d in items if d["state"]=="Booted"]
phones=[d for d in items if "iPhone" in d["name"]]
candidates=booted or phones or items
if not candidates:sys.exit("没有可用 iOS Simulator，请在 Xcode 下载 iOS 17+ runtime 并创建设备。")
print(candidates[0]["udid"])
'
}
