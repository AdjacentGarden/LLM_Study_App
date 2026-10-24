# Explicit rollback of the recognized native API task to the captured old SQLite task.
# Does not erase or restore business databases, config, model relay or old checkout.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$BackupRoot,
      [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$CurrentCommit,
      [string]$CurrentRepositoryRoot='',
      [string]$RuntimeRoot='C:\CloudPath\runtime')
$ErrorActionPreference='Stop'
if(-not $CurrentRepositoryRoot){$CurrentRepositoryRoot="C:\CloudPath\releases\$CurrentCommit\repository"}
$baseline=Get-Content -LiteralPath (Join-Path $BackupRoot 'baseline.json') -Raw | ConvertFrom-Json
$xmlPath=Join-Path $BackupRoot 'previous-task.xml'
if($baseline.kind -ne 'cloudpath-verified-previous-task' -or $baseline.database_backend -ne 'sqlite'){throw 'Automatic legacy-code rollback is only safe for the captured SQLite deployment; PG schema rollback requires its explicit backup plan.'}
foreach($pair in @(@($xmlPath,$baseline.xml_sha256),@($baseline.previous_launcher,$baseline.previous_launcher_sha256),@($baseline.env_file,$baseline.env_sha256))){
    if((Get-FileHash -LiteralPath $pair[0] -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pair[1]){throw 'Rollback baseline file changed; refusing to replace the task.'}
}
$task=Get-ScheduledTask -TaskName $baseline.task_name -ErrorAction Stop
$launcher=Join-Path $PSScriptRoot 'native-api-supervisor.ps1'
if(@($task.Actions).Count -ne 1 -or $task.Actions[0].Arguments -notlike ('*'+$launcher+'*') -or $task.Actions[0].Arguments -notlike ('*'+$CurrentCommit+'*') -or $task.Principal.UserId -notin @('SYSTEM','S-1-5-18')){throw 'Current task does not match the expected candidate release; no task replaced.'}
& $launcher -Action Stop -Commit $CurrentCommit -RepositoryRoot $CurrentRepositoryRoot -RuntimeRoot $RuntimeRoot -EnvFile $baseline.env_file
if(-not $?){throw 'Verified native API stop failed; rollback not started.'}
$deadline=(Get-Date).AddSeconds(30)
while((Get-ScheduledTask -TaskName $baseline.task_name).State -eq 'Running' -and (Get-Date)-lt $deadline){Start-Sleep -Milliseconds 250}
if((Get-ScheduledTask -TaskName $baseline.task_name).State -eq 'Running'){throw 'Task remains running; refusing to overwrite it.'}
Register-ScheduledTask -TaskName $baseline.task_name -Xml (Get-Content -LiteralPath $xmlPath -Raw) -Force | Out-Null
Remove-Item -LiteralPath (Join-Path $RuntimeRoot 'run\stop.request') -Force -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $baseline.task_name
[ordered]@{rollback_started=$true;restored_task=$baseline.task_name;business_data_overwritten=$false;configuration_changed=$false;relay_changed=$false} | ConvertTo-Json -Compress
