#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
usage() { cat <<'EOF'
Usage: scripts/test.sh [--device SIMULATOR_UDID | --destination 'platform=iOS Simulator,id=...'] [--unit|--ui|--all|--only TEST_IDENTIFIER] [--result PATH] [-- xcodebuild-options...]
Default: unit/integration XCTest (including native ONNX, imports and persistence).
--ui runs only UI tests; --all runs both. Explicit device avoids interfering with other Simulator work.
--result creates a new xcresult bundle; the path must not already exist.
Environment: BOOKCOURSE_DERIVED_DATA, BOOKCOURSE_PACKAGES, BOOKCOURSE_CONFIGURATION.
EOF
}
destination=''
scope=unit
result=''
selection=''
while (($#)); do
  case "$1" in
    --device) destination="platform=iOS Simulator,id=${2:?Missing UDID}"; shift 2 ;;
    --destination) destination="${2:?Missing destination}"; shift 2 ;;
    --unit|--ui|--all) scope="${1#--}"; shift ;;
    --only) selection="${2:?Missing test identifier}"; shift 2 ;;
    --result) result="${2:?Missing result path}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
bookcourse_xcode
if [[ -z "$destination" ]]; then destination="platform=iOS Simulator,id=$(bookcourse_simulator)"; fi
options=()
if [[ -n "$selection" ]]; then options+=("-only-testing:$selection"); else
  case "$scope" in unit) options+=('-only-testing:BookCourseTests');; ui) options+=('-only-testing:BookCourseUITests');; esac
fi
if [[ -n "$result" ]]; then
  [[ ! -e "$result" ]] || { echo "Result bundle already exists: $result" >&2; exit 2; }
  mkdir -p "$(dirname "$result")"
  options+=(-resultBundlePath "$result")
fi
python3 "$BOOKCOURSE_ROOT/scripts/validate-content.py"
exec xcodebuild -project "$BOOKCOURSE_ROOT/BookCourse.xcodeproj" -scheme BookCourse \
  -configuration "$BOOKCOURSE_CONFIGURATION" -destination "$destination" \
  -derivedDataPath "$BOOKCOURSE_DERIVED_DATA" -clonedSourcePackagesDirPath "$BOOKCOURSE_PACKAGES" \
  -disableAutomaticPackageResolution -parallel-testing-enabled NO -collect-test-diagnostics never test ${options[@]+"${options[@]}"} CODE_SIGNING_ALLOWED=NO "$@"
