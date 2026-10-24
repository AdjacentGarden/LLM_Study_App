#!/usr/bin/env bash
set -euo pipefail
ios_dir="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ios_dir/.build/native-voice-live"
swiftc -swift-version 6 -parse-as-library "$ios_dir"/Sources/LearningCore/*.swift "$ios_dir/scripts/native-voice-smoke.swift" -o "$ios_dir/.build/native-voice-live/check"
"$ios_dir/.build/native-voice-live/check"
