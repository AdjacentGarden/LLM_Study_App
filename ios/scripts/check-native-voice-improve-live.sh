#!/usr/bin/env bash
set -euo pipefail
ios_dir="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ios_dir/.build/native-voice-improve"
swiftc -swift-version 6 -parse-as-library "$ios_dir"/Sources/LearningCore/*.swift "$ios_dir/scripts/native-voice-improve-smoke.swift" -o "$ios_dir/.build/native-voice-improve/check"
"$ios_dir/.build/native-voice-improve/check" "$@"
