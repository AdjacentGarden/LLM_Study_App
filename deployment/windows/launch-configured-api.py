"""API-only Windows production entry; existing app.env remains authoritative.

Preflight performs no DDL, mail, model call, OCR or queue mutation. Launch keeps
all configuration values, refuses unsafe host/data settings, and needs no React.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path


class ConfigurationRefused(RuntimeError):
    pass


def configure(args) -> dict[str, str]:
    from dotenv import dotenv_values
    values = {key: value for key, value in dotenv_values(args.env_file, interpolate=False, encoding="utf-8-sig").items()
              if value is not None}
    if values.get('APP_HOST', '127.0.0.1') != '127.0.0.1':
        raise ConfigurationRefused('host_must_be_loopback')
    if values.get('APP_PORT', str(args.port)) != str(args.port):
        raise ConfigurationRefused('configured_port_mismatch')
    data = values.get('APP_DATA_DIR', '')
    if not data or not Path(data).is_absolute():
        raise ConfigurationRefused('explicit_absolute_existing_data_path_required')
    if Path(data).resolve() != (args.runtime_root / 'data').resolve():
        raise ConfigurationRefused('existing_runtime_data_identity_mismatch')
    # Preserve the operator-authorized existing runtime profile, including preview.
    # Configuration values are reported only as non-secret mode flags below.
    if values.get('OCR_WORKER_ENABLED', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}:
        raise ConfigurationRefused('windows_local_gpu_ocr_not_supported')
    system = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'PATH', 'COMSPEC',
              'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
              'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE') if key in os.environ}
    os.environ.clear()
    os.environ.update(system)
    os.environ.update(values)
    # Runtime/host/provider/database choices are neither replaced nor defaulted here.
    os.environ.update(PYTHONPATH=str(args.repository / 'backend/src'), PYTHONUNBUFFERED='1',
                      PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    sys.path.insert(0, str(args.repository / 'backend/src'))
    os.chdir(args.repository)
    return values


def queue_counts(args) -> dict[str, int]:
    from adaptive_learning.database import connect, database_url, healthcheck
    healthcheck()  # PG refuses an unapplied schema; it never falls back to SQLite.
    counts = {}
    definitions = [('studio', 'community.sqlite3', 'studio_jobs', "status IN ('queued','running','planning','submitting','polling','reviewing')"),
                   ('studio_uncertain', 'community.sqlite3', 'studio_jobs', "status = 'uncertain'"),
                   ('course', 'course_jobs.sqlite3', 'course_jobs', "status IN ('queued','running')"),
                   ('preparation', 'preparation.sqlite3', 'preparation', "status IN ('queued','running')"),
                   ('ocr', 'ocr_jobs.sqlite3', 'ocr_jobs', "status IN ('queued','running','retry_wait')")]
    for name, filename, table, condition in definitions:
        path = args.runtime_root / 'data/state' / filename
        if database_url() is None:
            if not path.exists():
                counts[name] = 0
                continue
            # Read-only SQLite connection: preflight must not create queue files.
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                counts[name] = int(db.execute(f'SELECT count(*) FROM {table} WHERE {condition}').fetchone()[0]) if exists else 0
        else:
            with connect(path) as db:
                counts[name] = int(db.execute(f'SELECT count(*) FROM {table} WHERE {condition}').fetchone()[0])
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', required=True, choices=['adaptive_learning.api.app:app'])
    parser.add_argument('--host', required=True, choices=['127.0.0.1'])
    parser.add_argument('--port', type=int, default=8100, choices=[8100])
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path, default=Path(r'C:\CloudPath\runtime'))
    parser.add_argument('--log', type=Path)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--require-empty-queues', action='store_true')
    args = parser.parse_args()
    args.repository = args.repository.resolve()
    args.runtime_root = args.runtime_root.resolve()
    if not args.env_file.is_file() or not (args.repository / 'backend/src/adaptive_learning/api/app.py').is_file():
        raise ConfigurationRefused('required_config_or_source_missing')
    before = hashlib.sha256(args.env_file.read_bytes()).hexdigest()
    configure(args)
    if args.preflight_only:
        counts = queue_counts(args)
        if args.require_empty_queues and any(counts.values()):
            raise ConfigurationRefused('pending_work_requires_explicit_recovery_decision')
        print(json.dumps({'preflight_passed': True, 'api_only': True, 'queue_counts': counts,
                          'config_sha256': before, 'config_unchanged': before == hashlib.sha256(args.env_file.read_bytes()).hexdigest(),
                          'runtime_profile': os.getenv('APP_ENV', 'development'),
                          'auth_demo_mode': os.getenv('AUTH_DEMO_MODE', '0') == '1',
                          'native_local_login_enabled': os.getenv('NATIVE_LOCAL_LOGIN_ENABLED', '0') == '1',
                          'schema_migrated_by_preflight': False, 'supplier_calls': 0, 'mail_sent': 0}))
        return 0
    if args.log is None:
        raise ConfigurationRefused('explicit_log_path_required')
    args.log.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(descriptor, 1)
    os.dup2(descriptor, 2)
    os.close(descriptor)
    import uvicorn
    uvicorn.run(args.app, host=args.host, port=args.port, workers=1,
                proxy_headers=False, access_log=False)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - redact the process-entry failure boundary
        # Intentionally do not dump credentials, environment values or DB driver messages.
        print(json.dumps({'started': False, 'error_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, ConfigurationRefused) else 'inspect_protected_runtime_logs'}))
        sys.exit(1)
