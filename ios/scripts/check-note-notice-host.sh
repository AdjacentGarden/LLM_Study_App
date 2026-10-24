#!/usr/bin/env bash
set -euo pipefail
ios_dir="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ios_dir/.build/note-notice-check"
swiftc -swift-version 6 -parse-as-library "$ios_dir"/Sources/LearningCore/*.swift "$ios_dir/scripts/validate-note-notice.swift" -o "$ios_dir/.build/note-notice-check/check"
"$ios_dir/.build/note-notice-check/check" "$ios_dir/note-notice-host-results.json"
