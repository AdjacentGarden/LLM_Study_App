# Read the recognized old task and hashes; only writes a new protected backup directory.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$BackupRoot,
      [string]$TaskName='CloudPath-PrivatePreview',
      [string]$EnvFile='C:\CloudPath\config\app.env',
      [string]$PreviousLauncher='C:\CloudPath\config\start-cloudpath.ps1')
$ErrorActionPreference='Stop'
if (Test-Path -LiteralPath $BackupRoot) { throw 'Rollback baseline already exists; never overwrite it.' }
$task=Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$powerShell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ine $powerShell -or $task.Actions[0].Arguments -notlike ('*'+$PreviousLauncher+'*') -or $task.Principal.UserId -notin @('SYSTEM','S-1-5-18')) { throw 'Existing task identity is not the recognized old CloudPath supervisor.' }
foreach($file in @($EnvFile,$PreviousLauncher)) { if(-not(Test-Path -LiteralPath $file -PathType Leaf)){throw 'Existing rollback file missing.'} }
New-Item -ItemType Directory -Path $BackupRoot | Out-Null
& icacls.exe $BackupRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Could not restrict rollback backup ACL.'}
$xmlPath=Join-Path $BackupRoot 'previous-task.xml'
Export-ScheduledTask -TaskName $TaskName | Set-Content -LiteralPath $xmlPath -Encoding UTF8
$postgresConfigured=$false
foreach($line in Get-Content -LiteralPath $EnvFile){
    if($line -match '^\s*(DATABASE_URL|DATABASE_HOST)\s*=\s*(.*)$'){
        $value=$Matches[2].Trim().Trim('"').Trim("'")
        if($value){$postgresConfigured=$true}
    }
}
$record=[ordered]@{kind='cloudpath-verified-previous-task';task_name=$TaskName;captured_utc=(Get-Date).ToUniversalTime().ToString('o');previous_launcher=$PreviousLauncher;previous_launcher_sha256=(Get-FileHash -LiteralPath $PreviousLauncher -Algorithm SHA256).Hash.ToLowerInvariant();env_file=$EnvFile;env_sha256=(Get-FileHash -LiteralPath $EnvFile -Algorithm SHA256).Hash.ToLowerInvariant();xml_sha256=(Get-FileHash -LiteralPath $xmlPath -Algorithm SHA256).Hash.ToLowerInvariant();database_backend=$(if($postgresConfigured){'postgresql'}else{'sqlite'});data_restored_by_rollback=$false}
$record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $BackupRoot 'baseline.json') -Encoding UTF8
[ordered]@{captured=$true;backup_root=$BackupRoot;task_name=$TaskName;database_backend=$record.database_backend} | ConvertTo-Json -Compress
