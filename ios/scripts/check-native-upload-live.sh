#!/usr/bin/env bash
set -euo pipefail
ios_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ios_dir"
mkdir -p .build
swiftc -swift-version 6 -parse-as-library Sources/LearningCore/*.swift scripts/native-upload-smoke.swift -o .build/native-upload-smoke
.build/native-upload-smoke
