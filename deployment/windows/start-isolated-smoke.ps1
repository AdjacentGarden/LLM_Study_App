# Windows PowerShell 5.1. This manages only a fresh candidate; it never reads app.env.
[CmdletBinding()]
param(
    [ValidateSet('Start','Stop','Restart','Status')][string]$Action = 'Start',
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$Commit,
    [string]$RepositoryRoot = '',
    [string]$RuntimeRoot = '',
    [string]$PythonExe = '',
    [string]$GitExe = 'C:\Program Files\Git\cmd\git.exe',
    [ValidateRange(1024,65535)][int]$Port = 8101,
    [ValidateRange(10,300)][int]$StartupTimeoutSeconds = 90
)
$ErrorActionPreference = 'Stop'
if (-not $RepositoryRoot) { $RepositoryRoot = "C:\CloudPath\releases\$Commit\repository" }
if (-not $RuntimeRoot) { $RuntimeRoot = "C:\CloudPath\validation\$Commit" }
$RepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot)
$RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
if ($Port -eq 8100) { throw 'Isolated smoke must not use production port 8100.' }
if ($RuntimeRoot.TrimEnd('\') -ieq 'C:\CloudPath\runtime') { throw 'Isolated smoke cannot use production runtime.' }
if (-not $PythonExe) { $PythonExe = Join-Path $RepositoryRoot 'backend\.venv\Scripts\python.exe' }
$PythonExe = [IO.Path]::GetFullPath($PythonExe)
$StateFile = Join-Path $RuntimeRoot 'run\smoke-backend-state.json'
$MarkerFile = Join-Path $RuntimeRoot 'isolated-smoke.json'
function Test-OwnedProcess([object]$State) {
    $process = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f [int]$State.backend_pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    if ($process.ExecutablePath -ine $PythonExe) { throw 'Candidate PID belongs to another executable; refusing to stop it.' }
    if ($process.CreationDate.ToUniversalTime().ToString('o') -ne $State.created_utc) { throw 'Candidate PID creation time changed; refusing to stop it.' }
    if ($process.CommandLine -notmatch 'adaptive_learning\.api\.app:app' -or $process.CommandLine -notmatch ("--port\s+{0}(\s|$)" -f $Port) -or $process.CommandLine -notmatch '--host\s+127\.0\.0\.1') {
        throw 'Candidate PID command does not match the loopback API; refusing to stop it.'
    }
    return $true
}
function Stop-Candidate {
    if (-not (Test-Path -LiteralPath $StateFile -PathType Leaf)) { return }
    $state = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    if ($state.commit -ne $Commit -or [int]$state.port -ne $Port -or $state.repository_root -ine $RepositoryRoot) { throw 'Candidate state identity mismatch.' }
    if (Test-OwnedProcess $state) {
        # Windows virtualenv launchers can own the real interpreter as a child.
        # Terminate only the process tree whose root identity was just verified.
        $previousErrorAction = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $null = & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /PID ([int]$state.backend_pid) /T /F 2>&1
        } finally { $ErrorActionPreference = $previousErrorAction }
        Wait-Process -Id ([int]$state.backend_pid) -Timeout 20 -ErrorAction SilentlyContinue
        if (Get-Process -Id ([int]$state.backend_pid) -ErrorAction SilentlyContinue) { throw 'Candidate did not exit; no foreign process was stopped.' }
        if (@(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).Count -gt 0) { throw 'Candidate port remains occupied after stopping its verified tree; no other process was stopped.' }
    }
    Remove-Item -LiteralPath $StateFile -Force
}
if ($Action -eq 'Stop' -or $Action -eq 'Restart') { Stop-Candidate }
if ($Action -eq 'Stop') { Write-Output '{"stopped":true,"scope":"isolated-candidate"}'; exit 0 }
if ($Action -eq 'Status') {
    $running = $false
    if (Test-Path -LiteralPath $StateFile) { $running = Test-OwnedProcess (Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json) }
    [ordered]@{running=$running;commit=$Commit;port=$Port;runtime_root=$RuntimeRoot} | ConvertTo-Json -Compress
    exit 0
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Candidate virtual environment is missing.' }
if (-not (Test-Path -LiteralPath (Join-Path $RepositoryRoot 'backend\src\adaptive_learning\api\app.py'))) { throw 'Candidate backend source is missing.' }
if (-not (Test-Path -LiteralPath $GitExe -PathType Leaf)) { throw 'Explicit Git executable is missing; supply GitExe.' }
$actualCommit = (& $GitExe -c ('safe.directory=' + $RepositoryRoot) -C $RepositoryRoot rev-parse HEAD 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $Commit) { throw 'Candidate checkout does not match the requested commit.' }
if (Test-Path -LiteralPath $MarkerFile) {
    $marker = Get-Content -LiteralPath $MarkerFile -Raw | ConvertFrom-Json
    if ($marker.kind -ne 'isolated-native-smoke' -or $marker.commit -ne $Commit -or [int]$marker.port -ne $Port) { throw 'Runtime marker does not match this isolated candidate.' }
} else {
    if ((Test-Path -LiteralPath $RuntimeRoot) -and @(Get-ChildItem -LiteralPath $RuntimeRoot -Force).Count -gt 0) { throw 'Refusing to initialize a nonempty, unmarked runtime.' }
    New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
    & icacls.exe $RuntimeRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict the isolated runtime ACL.' }
    [ordered]@{kind='isolated-native-smoke';commit=$Commit;port=$Port} | ConvertTo-Json | Set-Content -LiteralPath $MarkerFile -Encoding UTF8
}
foreach ($directory in @('data','run','logs')) { New-Item -ItemType Directory -Path (Join-Path $RuntimeRoot $directory) -Force | Out-Null }
if (Test-Path -LiteralPath $StateFile) {
    if (Test-OwnedProcess (Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json)) { throw 'Candidate already running; use Restart explicitly.' }
    Remove-Item -LiteralPath $StateFile -Force
}
if (@(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).Count -gt 0) { throw 'Candidate port already has a listener; no process was stopped.' }
# Build an allowlisted child environment. Supplier/database/mail/proxy keys never inherit.
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = $PythonExe
$start.WorkingDirectory = $RepositoryRoot
$entry = Join-Path $PSScriptRoot 'launch-isolated-api.py'
if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw 'Missing isolated API entry script beside this supervisor.' }
$log = Join-Path $RuntimeRoot 'logs\isolated-api.log'
$start.Arguments = '"' + $entry + '" --app adaptive_learning.api.app:app --host 127.0.0.1 --port ' + $Port + ' --log "' + $log + '"'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.EnvironmentVariables.Clear()
foreach ($name in @('SystemRoot','WINDIR','TEMP','TMP','PATH','COMSPEC','ProgramFiles','ProgramFiles(x86)','ProgramData','USERPROFILE','APPDATA','LOCALAPPDATA','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE')) {
    $value = [Environment]::GetEnvironmentVariable($name,'Process')
    if ($null -ne $value) { $start.EnvironmentVariables[$name] = $value }
}
$settings = @{
    PYTHONPATH=(Join-Path $RepositoryRoot 'backend\src'); PYTHONUNBUFFERED='1'; PYTHONUTF8='1';
    PYTHONIOENCODING='utf-8'; APP_ENV='development'; APP_HOST='127.0.0.1'; APP_PORT=[string]$Port;
    APP_DATA_DIR=(Join-Path $RuntimeRoot 'data'); OCR_WORKER_ENABLED='0'; STUDIO_WORKER_ENABLED='1';
    NATIVE_LOCAL_LOGIN_ENABLED='1'; AUTH_DEMO_MODE='0'; LLM_PROVIDER='rules';
    DATABASE_URL=''; DATABASE_HOST=''; HF_HUB_OFFLINE='1'; TRANSFORMERS_OFFLINE='1';
    ORT_DISABLE_TELEMETRY='1'; NO_PROXY='127.0.0.1,localhost,::1'
}
foreach ($name in $settings.Keys) { $start.EnvironmentVariables[$name] = $settings[$name] }
$process = New-Object System.Diagnostics.Process
$process.StartInfo = $start
if (-not $process.Start()) { throw 'Candidate API process failed to start.' }
Start-Sleep -Milliseconds 300
$identity = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $process.Id) -ErrorAction SilentlyContinue
if (-not $identity) { throw 'Candidate exited before its identity could be recorded.' }
[ordered]@{kind='isolated-native-smoke';backend_pid=$process.Id;created_utc=$identity.CreationDate.ToUniversalTime().ToString('o');commit=$Commit;port=$Port;repository_root=$RepositoryRoot;python=$PythonExe} | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
$deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
$healthy = $false
try {
    while ((Get-Date) -lt $deadline) {
        if ($process.HasExited) { throw 'Candidate exited during startup.' }
        try {
            $health = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/api/health" -f $Port) -TimeoutSec 2
            if ($health.ok -eq $true) { $healthy = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 300
    }
    if (-not $healthy) { throw 'Candidate failed real worker health checks within its startup deadline.' }
} catch { Stop-Candidate; throw }
[ordered]@{started=$true;scope='isolated-candidate';commit=$Commit;backend_pid=$process.Id;port=$Port;runtime_root=$RuntimeRoot;health_ok=$true;supplier_keys_inherited=$false;ocr_enabled=$false;studio_thread_enabled=$true} | ConvertTo-Json -Compress
