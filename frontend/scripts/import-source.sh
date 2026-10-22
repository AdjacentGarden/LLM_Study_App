#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo 'Usage: scripts/import-source.sh /absolute/path/to/original-frontend'
  echo 'Explicit maintenance command: refreshes committed fixtures/resources/icons/fonts and provenance. Never copies .env or device data.'
  echo 'Requires original npm dependencies, Node 22.13+ / 24+, Python 3 + fonttools==4.60.2.'
  exit 0
fi
if [[ $# != 1 ]]; then echo 'Pass exactly one original repository path (see --help).' >&2; exit 2; fi
export BOOKCOURSE_SOURCE="$(cd "$1" && pwd)"
[[ "$BOOKCOURSE_SOURCE" != "$BOOKCOURSE_ROOT" ]] || { echo 'Source must be a separate read-only frontend repository.' >&2; exit 2; }
[[ -f "$BOOKCOURSE_SOURCE/src/data/generated/demo-state.json" && -d "$BOOKCOURSE_SOURCE/node_modules/typescript" ]] || { echo 'Source fixtures or installed TypeScript dependency missing. Install the source lockfile with npm ci in that repository first.' >&2; exit 2; }
cd "$BOOKCOURSE_ROOT"
node --loader ./scripts/source-loader.mjs ./scripts/export-data.mjs
python3 ./scripts/sync-source-resources.py --source "$BOOKCOURSE_SOURCE"
python3 ./scripts/prepare-fonts.py
node ./scripts/generate-tokenizer-golden.mjs
python3 ./scripts/refresh-licenses.py --source "$BOOKCOURSE_SOURCE" --packages "$BOOKCOURSE_PACKAGES"
python3 ./scripts/validate-content.py
echo 'Fixture refresh complete. Review Resources/ and References/ diffs before committing.'
