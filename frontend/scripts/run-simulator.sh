#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
usage() { cat <<'EOF'
Usage: scripts/run-simulator.sh [--device SIMULATOR_UDID] [--no-build] [-- app-launch-arguments...]
Boots an available iOS Simulator, builds/installs BookCourse, and launches it.
No device erasure or application-data reset is performed.
Example: scripts/run-simulator.sh --device UUID -- --fixture --route lesson --chapter c2s1 --fixture-session review
Environment: BOOKCOURSE_DERIVED_DATA, BOOKCOURSE_PACKAGES, BOOKCOURSE_CONFIGURATION.
EOF
}
device=''
build=true
while (($#)); do
  case "$1" in
    --device) device="${2:?Missing UDID}"; shift 2 ;;
    --no-build) build=false; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
bookcourse_xcode
if [[ -z "$device" ]]; then device="$(bookcourse_simulator)"; fi
state="$(xcrun simctl list devices available --json | python3 -c 'import json,sys; items=[d for ds in json.load(sys.stdin)["devices"].values() for d in ds if d["udid"]==sys.argv[1]]; print(items[0]["state"] if items else "Unavailable")' "$device")"
if [[ "$state" == Unavailable ]]; then echo "Simulator unavailable: $device" >&2; exit 2; fi
if [[ "$state" != Booted ]]; then xcrun simctl boot "$device"; fi
xcrun simctl bootstatus "$device" -b
if ! open -a Simulator >/dev/null 2>&1; then
  simulator_app="${DEVELOPER_DIR:-$(xcode-select -p)}/Applications/Simulator.app"
  if [[ -d "$simulator_app" ]]; then
    open "$simulator_app" || echo 'Simulator GUI 无法打开；继续安装和启动 App。' >&2
  else
    echo '未找到 Simulator GUI；继续通过 simctl 安装和启动 App。' >&2
  fi
fi
if "$build"; then "$BOOKCOURSE_ROOT/scripts/build.sh" --destination "platform=iOS Simulator,id=$device"; fi
app="$BOOKCOURSE_DERIVED_DATA/Build/Products/$BOOKCOURSE_CONFIGURATION-iphonesimulator/BookCourse.app"
if [[ ! -d "$app" ]]; then echo "App not found: $app. Run scripts/build.sh first." >&2; exit 1; fi
xcrun simctl install "$device" "$app"
xcrun simctl launch --terminate-running-process "$device" com.littleri.bookcourseai.swiftui "$@"
