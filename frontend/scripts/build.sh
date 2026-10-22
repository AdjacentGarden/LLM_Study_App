#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
usage() { cat <<'EOF'
Usage: scripts/build.sh [--destination 'generic/platform=iOS Simulator'] [--configuration Debug|Release] [--regenerate-project] [-- xcodebuild-options...]
Builds committed native sources/resources. Does not read the React repository.
Environment: BOOKCOURSE_DERIVED_DATA, BOOKCOURSE_PACKAGES, BOOKCOURSE_CONFIGURATION.
First build resolves the two pinned Swift packages and requires internet.
EOF
}
destination='generic/platform=iOS Simulator'
regenerate=false
while (($#)); do
  case "$1" in
    --destination) destination="${2:?Missing destination}"; shift 2 ;;
    --configuration) BOOKCOURSE_CONFIGURATION="${2:?Missing configuration}"; shift 2 ;;
    --regenerate-project) regenerate=true; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
bookcourse_xcode
if "$regenerate"; then python3 "$BOOKCOURSE_ROOT/scripts/generate-project.py"; fi
options=()
if [[ "$destination" == *Simulator* ]]; then options+=(CODE_SIGNING_ALLOWED=NO); fi
python3 "$BOOKCOURSE_ROOT/scripts/validate-content.py"
exec xcodebuild -project "$BOOKCOURSE_ROOT/BookCourse.xcodeproj" -scheme BookCourse \
  -configuration "$BOOKCOURSE_CONFIGURATION" -destination "$destination" \
  -derivedDataPath "$BOOKCOURSE_DERIVED_DATA" -clonedSourcePackagesDirPath "$BOOKCOURSE_PACKAGES" \
  -disableAutomaticPackageResolution build ${options[@]+"${options[@]}"} "$@"
