$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$root='C:\CloudPath\runtime\smtp-relay'
$python='C:\CloudPath\app\backend\.venv\Scripts\python.exe'
$entry=Join-Path $root 'windows_smtp_relay.py'
$credential='C:\CloudPath\runtime\llm-relay\credentials.dpapi'
$env:PYTHONPATH='C:\CloudPath\runtime\llm-relay\lib;C:\CloudPath\runtime\llm-relay'
$mutex=New-Object Threading.Mutex($false,'Global\CloudPath-SmtpRelay')
$owned=$false
$child=$null
try{
 try{$owned=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$owned=$true}
 if(-not $owned){throw 'Another SMTP relay supervisor already owns this service.'}
 Remove-Item (Join-Path $root 'stop.request') -Force -ErrorAction SilentlyContinue
 $failures=0
 while(-not(Test-Path (Join-Path $root 'stop.request'))){
  $arguments=@(('"{0}"' -f $entry),'--credential-file',('"{0}"' -f $credential),'--log-file',('"{0}"' -f (Join-Path $root 'smtp-relay.log')))
  $child=Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru
  Start-Sleep -Milliseconds 250
  $identity=Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $child.Id)
  if(-not $identity){throw 'SMTP child exited before identity was recorded.'}
  @{kind='cloudpath-smtp-relay';pid=$child.Id;created_utc=$identity.CreationDate.ToUniversalTime().ToString('o');python=$python;entry=$entry;supervisor_pid=$PID}|ConvertTo-Json|Set-Content (Join-Path $root 'state.json') -Encoding UTF8
  $started=Get-Date
  while(-not $child.HasExited -and -not(Test-Path (Join-Path $root 'stop.request'))){Start-Sleep -Seconds 1;$child.Refresh()}
  if(Test-Path (Join-Path $root 'stop.request')){break}
  if(((Get-Date)-$started).TotalSeconds -ge 300){$failures=0}
  $failures++
  if($failures -ge 3){throw 'SMTP process failed repeatedly; inspect protected logs.'}
  Start-Sleep -Seconds (5*$failures)
 }
}finally{
 if($owned){
  if($child -and (Test-Path (Join-Path $root 'state.json'))){
   $state=Get-Content (Join-Path $root 'state.json') -Raw|ConvertFrom-Json
   $identity=Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f [int]$state.pid) -ErrorAction SilentlyContinue
   if($identity -and $identity.ExecutablePath -ieq $state.python -and $identity.CreationDate.ToUniversalTime().ToString('o') -eq $state.created_utc -and $identity.CommandLine -like ('*'+$entry+'*')){
    & taskkill.exe /PID ([int]$state.pid) /T /F *> $null
   }
   Remove-Item (Join-Path $root 'state.json') -Force
  }
  $mutex.ReleaseMutex()
 }
 $mutex.Dispose()
}
