#!/usr/bin/env bash
set -euo pipefail

ios_dir="$(cd "$(dirname "$0")/.." && pwd)"
mode="${1:-check}"
case "$mode" in check|simulator|device|archive) ;; *) echo "Usage: $0 [check|simulator|device|archive]" >&2; exit 64 ;; esac

python3 "$ios_dir/scripts/validate-project.py"

if ! xcodebuild -version >/dev/null 2>&1; then
    echo "BLOCKED: full Xcode is required; Command Line Tools cannot compile an iOS application." >&2
    echo "Install Xcode, select its developer directory, then rerun this script. No iOS build or IPA was produced." >&2
    exit 69
fi
if ! xcrun --sdk iphoneos --show-sdk-path >/dev/null 2>&1; then
    echo "BLOCKED: selected Xcode has no iPhoneOS SDK. Install its iOS platform support. No iOS build was produced." >&2
    exit 69
fi
selected_major="$(xcodebuild -version | awk '/^Xcode / {split($2, parts, "."); print parts[1]}')"
if [[ -z "$selected_major" || "$selected_major" -lt 26 ]]; then
    echo "BLOCKED: this source requires Xcode 26 or newer, including the renamed AVAudioSession Bluetooth option." >&2
    echo "Choose a version with device support matching the iPhone's installed iOS release. No iOS build was produced." >&2
    exit 69
fi
xcodebuild -version
xcrun --sdk iphoneos --show-sdk-version
if [[ "$mode" == check ]]; then
    xcodebuild -list -project "$ios_dir/CloudPath.xcodeproj"
    echo "Toolchain and project discovery succeeded. This check does not compile, sign, or install the app."
    exit 0
fi

args=(-project "$ios_dir/CloudPath.xcodeproj" -scheme CloudPath -derivedDataPath "$ios_dir/.build/xcode")
if [[ "$mode" == simulator ]]; then
    xcodebuild "${args[@]}" -configuration Debug -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build
    echo "Simulator application compiled. No simulator was launched."
    exit 0
fi

if [[ -z "${DEVELOPMENT_TEAM:-}" ]]; then
    echo "BLOCKED: set DEVELOPMENT_TEAM to your own signing team ID for a device build, or use Xcode Signing & Capabilities." >&2
    echo "A signed, provisioned app is required for installation on a physical iPhone." >&2
    exit 78
fi
args+=(-destination 'generic/platform=iOS' "DEVELOPMENT_TEAM=$DEVELOPMENT_TEAM")
if [[ "$mode" == device ]]; then
    xcodebuild "${args[@]}" -configuration Debug build
    echo "Device application compiled and signed. Installation and device acceptance must be verified separately."
else
    xcodebuild "${args[@]}" -configuration Release -archivePath "$ios_dir/.build/CloudPath.xcarchive" archive
    echo "Archive created. An archive is not an exported installable IPA. Export requires your matching signing/provisioning method."
fi
