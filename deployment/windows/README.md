# Native API on Windows

Windows runs the FastAPI backend; `frontend/BookCourse.xcodeproj` is built and
signed with Xcode on macOS. No React `dist` or desktop browser is required.
The bundled biology course works without model inference. New PDF OCR, local
speech recognition and embedding models require their actual platform and file
adapters; a Linux model path is not a Windows service configuration.

Use a clean, pinned release checkout and Python 3.11+. Install
`python -m pip install -e "./backend[dev,pdf,media]"` in a new virtual environment.
Keep the previous interpreter/checkout, existing app.env, runtime and independent
model relay. Configured PostgreSQL requires the explicit migrations in the
PostgreSQL runbook; the startup entry never chooses SQLite as a fallback.

## Validate a candidate

`start-isolated-smoke.ps1 -Commit <40-character-sha>` creates an isolated runtime,
listens on loopback port 8101, and supports `Start`, `Stop`, `Restart` and `Status`.
Pass `RepositoryRoot`, `RuntimeRoot` and `PythonExe` when using other paths.
It builds an allowlisted environment, inherits no provider or database secrets,
disables OCR, and runs the real empty Studio worker. Development default login
is restricted to loopback. It does not read or alter the existing app.env.

Run the HTTP acceptance script with the candidate interpreter:

```powershell
python deployment/windows/native-http-acceptance.py --phase before `
  --base-url http://127.0.0.1:8101 --commit <sha> --receipt <private-runtime>/native-receipt.json
# Restart the exact same isolated candidate, then:
python deployment/windows/native-http-acceptance.py --phase after `
  --base-url http://127.0.0.1:8101 --commit <sha> --receipt <private-runtime>/native-receipt.json
```

The script verifies preferences, packaged-course import, objective grading,
flashcard scheduling, idempotent progress and PDF byte hashes. It does not call
model-backed QA, generate new teaching content, run OCR or send email. The private
receipt directory holds a session file; keep its ACL restricted and do not commit
it. A successful run is backend/persistence acceptance, not iPhone installation
or GPU/model-quality acceptance.

## Replace an existing owned service

First capture the existing task with `capture-rollback-baseline.ps1 -BackupRoot
<new-private-directory>`, and make consistent SQLite backups while its owned API
is stopped. Never overwrite backups. Run `launch-configured-api.py` with
`--preflight-only --require-empty-queues` and the explicit `--repository`,
`--env-file`, `--runtime-root`, `--app adaptive_learning.api.app:app`,
`--host 127.0.0.1`, `--port 8100` arguments.

`native-api-supervisor.ps1 -Action Serve -Commit <sha>` is the API-only SYSTEM
entry for a startup scheduled task. It preserves the configured runtime profile
(including an existing private preview profile), auth mode, database choice and
provider settings. It verifies release/PID/executable/creation-time ownership,
requires loopback, and uses the existing data directory. It does not migrate
PostgreSQL, configure missing models, create credentials or install the task.

Replace only the recognized, stopped application task after candidate tests and
preflight pass. Retain the existing XML and launcher for rollback, then confirm
real worker health, persistence and independent relay state. `Action Stop`
requests a verified stop; `Action Status` reads the exact release status.
`restore-previous-task.ps1` can restore the captured old SQLite task without
restoring or erasing business data. PostgreSQL rollback requires its own explicit
database backup plan.
