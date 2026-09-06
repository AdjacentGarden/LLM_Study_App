# Stage 0 synthetic fixtures

This directory contains synthetic, non-sensitive fixtures used to establish the CloudPath parser and RAG baseline.

Generate or refresh the files with the isolated Python 3.12 test environment:

```powershell
& "$env:TEMP\cloudpath-stage0-venv\Scripts\python.exe" quality\stage0\generate_fixtures.py
```

`fixture_manifest.json` records scenario labels, byte sizes, and SHA-256 hashes. The fixtures contain no user documents or personal data.

The `reject` samples must never be sent to a production parser. They exist only for upload-validation tests in later approved stages.
