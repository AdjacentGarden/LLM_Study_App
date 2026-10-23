# SYSTEM API-only supervisor. No frontend/dist prerequisite; existing config is read-only.
[CmdletBinding()]
param(
    [ValidateSet('Serve','Stop','Status')][string]$Action = 'Serve',
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$Commit,
    [string]$RepositoryRoot = '',
    [string]$RuntimeRoot = 'C:\CloudPath\runtime',
    [string]$EnvFile = 'C:\CloudPath\config\app.env',
    [string]$PythonExe = '',
    [string]$GitExe = 'C:\Program Files\Git\cmd\git.exe'
)
$ErrorActionPreference = 'Stop'
if (-not $RepositoryRoot) { $RepositoryRoot = "C:\CloudPath\releases\$Commit\repository" }
$RepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot)
$RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
if (-not $PythonExe) { $PythonExe = Join-Path $RepositoryRoot 'backend\.venv\Scripts\python.exe' }
$PythonExe = [IO.Path]::GetFullPath($PythonExe)
$Entry = Join-Path $PSScriptRoot 'launch-configured-api.py'
$StateFile = Join-Path $RuntimeRoot 'run\native-api-state.json'
$StopFile = Join-Path $RuntimeRoot 'run\native-api.stop.request'
$SupervisorLog = Join-Path $RuntimeRoot 'logs\native-api-supervisor.log'
$backend = $null
$backendState = $null
$hasMutex = $false
$mutex = New-Object System.Threading.Mutex($false, 'Global\CloudPath-PrivatePreview-Supervisor')
function Write-PrivateLog([string]$Message) {
    Add-Content -LiteralPath $SupervisorLog -Value ('{0} {1}' -f (Get-Date -Format 'o'), $Message) -Encoding UTF8
}
function Test-OwnedBackend([object]$State) {
    if ($State.kind -ne 'cloudpath-native-api' -or $State.commit -ne $Commit -or $State.repository_root -ine $RepositoryRoot -or $State.python -ine $PythonExe) { throw 'API state belongs to another release; use its exact rollback/stop command.' }
    $process = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f [int]$State.backend_pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    if ($process.ExecutablePath -ine $PythonExe -or $process.CreationDate.ToUniversalTime().ToString('o') -ne $State.created_utc) { throw 'API PID executable/time identity mismatch; refusing to terminate it.' }
    if ($process.CommandLine -notlike ('*' + $Entry + '*') -or $process.CommandLine -notmatch '--app\s+adaptive_learning\.api\.app:app' -or $process.CommandLine -notmatch '--host\s+127\.0\.0\.1' -or $process.CommandLine -notmatch '--port\s+8100(\s|$)') { throw 'API PID command identity mismatch; refusing to terminate it.' }
    return $true
}
function Test-OwnedSupervisor([object]$State) {
    $process = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f [int]$State.supervisor_pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    $powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if ($process.ExecutablePath -ine $powerShell -or $process.CreationDate.ToUniversalTime().ToString('o') -ne $State.supervisor_created_utc -or $process.CommandLine -notlike ('*' + $PSCommandPath + '*') -or $process.CommandLine -notlike ('*' + $Commit + '*')) { throw 'Supervisor identity mismatch; no process was stopped.' }
    return $true
}
function Stop-OwnedBackend([object]$State) {
    if (Test-OwnedBackend $State) {
        $previousErrorAction = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $null = & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /PID ([int]$State.backend_pid) /T /F 2>&1
        } finally { $ErrorActionPreference = $previousErrorAction }
        Wait-Process -Id ([int]$State.backend_pid) -Timeout 20 -ErrorAction SilentlyContinue
        if (Get-Process -Id ([int]$State.backend_pid) -ErrorAction SilentlyContinue) { throw 'Verified API process did not exit.' }
        if (@(Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue).Count -gt 0) { throw 'API port remains occupied after stopping its verified tree; no other process was stopped.' }
    }
}
if ($Action -eq 'Status') {
    $running = $false
    if (Test-Path -LiteralPath $StateFile) { $running = Test-OwnedBackend (Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json) }
    [ordered]@{running=$running;commit=$Commit;port=8100;api_only=$true} | ConvertTo-Json -Compress
    $mutex.Dispose()
    exit 0
}
if ($Action -eq 'Stop') {
    if (Test-Path -LiteralPath $StateFile) {
        $state = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
        $owned = Test-OwnedBackend $state
        if (Test-OwnedSupervisor $state) {
            New-Item -ItemType File -Path $StopFile -Force | Out-Null
            $deadline = (Get-Date).AddSeconds(30)
            while ((Test-Path -LiteralPath $StateFile) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
            if (Test-Path -LiteralPath $StateFile) { throw 'Owned supervisor did not stop within 30 seconds; no task was forcibly replaced.' }
        } else {
            if ($owned) { Stop-OwnedBackend $state }
            Remove-Item -LiteralPath $StateFile -Force
        }
    }
    $mutex.Dispose()
    Write-Output '{"stopped":true,"scope":"verified-native-api"}'
    exit 0
}
try {
    try { $hasMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $hasMutex = $true }
    if (-not $hasMutex) { throw 'Another CloudPath supervisor still owns the service; stop its verified release first.' }
    foreach ($file in @($PythonExe, $EnvFile, $Entry, (Join-Path $RepositoryRoot 'backend\src\adaptive_learning\api\app.py'))) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'Required API deployment file is missing.' }
    }
    if (-not (Test-Path -LiteralPath $GitExe -PathType Leaf)) { throw 'Explicit Git executable is missing; supply GitExe.' }
$actualCommit = (& $GitExe -c ('safe.directory=' + $RepositoryRoot) -C $RepositoryRoot rev-parse HEAD 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $Commit) { throw 'Release checkout commit does not match requested commit.' }
    foreach ($directory in @((Join-Path $RuntimeRoot 'run'), (Join-Path $RuntimeRoot 'logs'))) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
    if (Test-Path -LiteralPath $StateFile) {
        $state = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
        if (Test-OwnedBackend $state) { Stop-OwnedBackend $state }
        Remove-Item -LiteralPath $StateFile -Force
    }
    if (@(Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue).Count -gt 0) { throw 'Port 8100 is occupied without verified native state; old service or foreign listener left untouched.' }
    # A successful explicit Serve after Stop starts a new service lifetime.
    Remove-Item -LiteralPath $StopFile -Force -ErrorAction SilentlyContinue
    $configHash = (Get-FileHash -LiteralPath $EnvFile -Algorithm SHA256).Hash.ToLowerInvariant()
    $failures = 0
    while (-not (Test-Path -LiteralPath $StopFile)) {
        if ((Get-FileHash -LiteralPath $EnvFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $configHash) { throw 'Runtime configuration changed while supervisor was active; restart explicitly to acknowledge it.' }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $log = Join-Path $RuntimeRoot "logs\native-api-$stamp.log"
        $arguments = @(('"{0}"' -f $Entry), '--app', 'adaptive_learning.api.app:app', '--host', '127.0.0.1', '--port', '8100', '--repository', ('"{0}"' -f $RepositoryRoot), '--env-file', ('"{0}"' -f $EnvFile), '--runtime-root', ('"{0}"' -f $RuntimeRoot), '--log', ('"{0}"' -f $log))
        $backend = Start-Process -FilePath $PythonExe -ArgumentList $arguments -WorkingDirectory $RepositoryRoot -WindowStyle Hidden -PassThru
        Start-Sleep -Milliseconds 250
        $identity = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $backend.Id) -ErrorAction SilentlyContinue
        $supervisorIdentity = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $PID) -ErrorAction Stop
        if (-not $identity) { throw 'API process exited before its identity was recorded; inspect protected runtime log.' }
        $backendState = [ordered]@{kind='cloudpath-native-api';commit=$Commit;repository_root=$RepositoryRoot;python=$PythonExe;backend_pid=$backend.Id;created_utc=$identity.CreationDate.ToUniversalTime().ToString('o');supervisor_pid=$PID;supervisor_created_utc=$supervisorIdentity.CreationDate.ToUniversalTime().ToString('o');config_sha256=$configHash;port=8100}
        $backendState | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
        Write-PrivateLog ('API started PID {0}; commit {1}' -f $backend.Id, $Commit)
        $started = Get-Date
        while (-not $backend.HasExited -and -not (Test-Path -LiteralPath $StopFile)) { Start-Sleep -Seconds 1; $backend.Refresh() }
        if (Test-Path -LiteralPath $StopFile) { break }
        $backend.WaitForExit()
        Write-PrivateLog ('API exited with code {0}' -f $backend.ExitCode)
        if (((Get-Date) - $started).TotalSeconds -ge 300) { $failures = 0 }
        $failures++
        if ($failures -ge 3) { throw 'API failed three times; leaving service stopped for a concrete repair or rollback.' }
        for ($i=0; $i -lt 5*$failures -and -not (Test-Path -LiteralPath $StopFile); $i++) { Start-Sleep -Seconds 1 }
    }
} finally {
    if ($hasMutex) {
        if ($backendState) { Stop-OwnedBackend $backendState }
        if (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile -Force }
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
