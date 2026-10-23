#!/usr/bin/env bash
set -euo pipefail
ios_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ios_dir"
swift run CoreChecks --report core-test-results.json
swiftc -swift-version 6 -parse-as-library -typecheck Sources/LearningCore/*.swift App/*.swift
./scripts/check-audio-host.sh
./scripts/check-app-model-host.sh
bash ./scripts/check-voice-review-host.sh
bash ./scripts/check-note-notice-host.sh
python3 scripts/validate-project.py
set +e
./scripts/build-ios.sh check > ios-toolchain-check.log 2>&1
ios_status=$?
set -e
python3 - "$ios_status" <<'PY'
import json, platform, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
status = int(sys.argv[1])
report = {
    'created_at': datetime.now(timezone.utc).isoformat(),
    'platform': platform.platform(),
    'swift': subprocess.check_output(['swift','--version'],text=True).strip(),
    'core_checks': json.loads(Path('core-test-results.json').read_text()),
    'app_model_checks': json.loads(Path('app-model-test-results.json').read_text()),
    'voice_review_app_model_checks': json.loads(Path('voice-review-app-model-results.json').read_text()),
    'note_notice_checks': json.loads(Path('note-notice-host-results.json').read_text()),
    'swiftui_macos_typecheck': 'passed; only macOS SDK branches compiled',
    'audio_file_recovery': 'passed; actual generated AAC, no microphone or playback',
    'project_and_plist_static': 'passed',
    'ios_toolchain_check_exit': status,
    'ios_build_performed': False,
    'signed_ipa_produced': False,
    'iphone_installation_or_ui_test': False,
    'limits': ['No iPhoneOS SDK/complete Xcode on this host', 'Mac typecheck does not compile iOS-only branches', 'Fixture tests do not prove live API or model quality', 'Physical iPhone permission/lifecycle acceptance pending'],
}
Path('host-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print('Host checks passed. iOS toolchain check exit:', status)
PY
if [[ "$ios_status" != 0 && "$ios_status" != 69 ]]; then
    cat ios-toolchain-check.log >&2
    exit "$ios_status"
fi
