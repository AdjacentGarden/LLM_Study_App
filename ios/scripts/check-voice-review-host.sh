#!/usr/bin/env bash
set -euo pipefail
ios_dir="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ios_dir/.build/voice-review-check"
swiftc -swift-version 6 -parse-as-library "$ios_dir"/Sources/LearningCore/*.swift "$ios_dir/App/AppModel.swift" "$ios_dir/scripts/validate-voice-review.swift" -o "$ios_dir/.build/voice-review-check/check"
"$ios_dir/.build/voice-review-check/check"
