# Native API on Windows

Windows runs the FastAPI backend; `frontend/BookCourse.xcodeproj` is built and
signed with Xcode on macOS. No React `dist` or desktop browser is required.
Published community courses come from the backend catalog; an empty catalog is
valid. Bundled curriculum files are retained as authored resources and are not
automatically inserted into a user's library. New PDF OCR, local
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

## Windows gateway to the GPU backend

For the single-user LAN/VPN deployment, keep one authoritative business API and
data directory on the 4090 Linux server. Windows can run
`cloudpath-gateway.py --config <private-config.json> --log <private-log>` using the
release's Python environment. The configuration contains only these fields:

```json
{"upstream_url":"http://10.249.186.201:18126","host":"0.0.0.0","port":8100}
```

`native-gateway-supervisor.ps1 -Action Serve -Commit <sha>` checks the pinned
release and owns only its verified Python process tree. It supports `Stop` and
`Status` without opening the SYSTEM mutex. Configuration defaults to
`C:\CloudPath\config\gateway.json`; runtime state/logs default to
`C:\CloudPath\gateway-runtime`. A separate loopback candidate may use port 8101,
an explicit configuration path and a separate runtime root. Restrict the task,
configuration, state and logs to SYSTEM/Administrators. Allow inbound 8100 only
for the intended LAN/VPN sources and Private/Domain firewall profiles.

The gateway streams uploads/downloads and preserves authentication cookies,
multiple `Set-Cookie` headers, raw paths/queries, status codes and compressed
bytes. Its shared connection pool never stores cookies. It does not retry
requests, cache PDFs, host login accounts, run models, or fall back to Windows
demo data. Client `Host`/`Origin` remain paired for backend same-origin checks;
untrusted forwarding headers are removed. The upstream connection is to the
fixed configured private IP, independent of `Host`.

Before replacing the old API, capture its task XML/config and stop its verified
supervisor, then back up every SQLite database and non-database file together
while all old writers are stopped. Keep these Windows files for rollback; do
not merge them into Linux by overwriting the authoritative user data. Keep the
independent `CloudPathModelRelay` running. Validate the Linux upstream before
starting the new SYSTEM gateway task and test a verified stop/start afterward.
Rollback stops only the owned gateway, removes its specific firewall rule, then
restores the captured old task. It never restores old Windows data over Linux.

For SMTP blocked on Linux, `deployment/server/windows_smtp_relay.py` provides a
separate fixed-target SSH tunnel from Linux loopback 18465 to `smtp.qq.com:465`.
It reuses the existing protected DPAPI credential file and pinned SSH host key,
without restarting the model relay. The backend must keep `SMTP_HOST=smtp.qq.com`
for TLS hostname validation and use its explicit SMTP tunnel TCP endpoint fields;
changing the TLS hostname to loopback or disabling certificate checks is invalid.
