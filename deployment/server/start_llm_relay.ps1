$ErrorActionPreference='Stop'
$env:PYTHONPATH='C:\CloudPath\runtime\llm-relay\lib'
& 'C:\CloudPath\app\backend\.venv\Scripts\python.exe' 'C:\CloudPath\runtime\llm-relay\windows_llm_relay.py' --serve --credential-file 'C:\CloudPath\runtime\llm-relay\credentials.dpapi' --log-file 'C:\CloudPath\runtime\llm-relay\relay.log'
exit $LASTEXITCODE
