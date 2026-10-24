#!/usr/bin/env bash
set -euo pipefail
ios_dir="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ios_dir/.build/audio-check"
swiftc -swift-version 6 -parse-as-library "$ios_dir/App/AudioCapture.swift" "$ios_dir/scripts/validate-audio.swift" -o "$ios_dir/.build/audio-check/check"
"$ios_dir/.build/audio-check/check"
