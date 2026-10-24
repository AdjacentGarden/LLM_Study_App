$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Read-Endpoint([string] $url) {
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 8
        $body = $response.Content | ConvertFrom-Json
        return [ordered]@{url=$url;reachable=$true;status=[int]$response.StatusCode;elapsed_ms=[Math]::Round($clock.Elapsed.TotalMilliseconds,1);ok=$body.ok;service=$body.service;check_scope=$body.check_scope;external_inference_verified=$body.external_inference_verified;features=$body.features}
    } catch {
        return [ordered]@{url=$url;reachable=$false;elapsed_ms=[Math]::Round($clock.Elapsed.TotalMilliseconds,1);error=$_.Exception.Message}
    }
}
$endpoints = @(
    (Read-Endpoint 'http://10.249.186.201:18126/api/health'),
    (Read-Endpoint 'http://10.249.186.201:18126/api/readiness'),
    (Read-Endpoint 'http://10.249.186.201:8100/api/health')
)
$relay = Get-ScheduledTask -TaskName 'CloudPathModelRelay' -ErrorAction SilentlyContinue
$relayInfo = if ($relay) { Get-ScheduledTaskInfo -TaskName 'CloudPathModelRelay' } else { $null }
$relayProcesses = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | Where-Object {$_.CommandLine -match 'windows_llm_relay.py'} | Select-Object ProcessId,Name)
$sshConnections = @(Get-NetTCPConnection -State Established -RemoteAddress '10.249.186.201' -RemotePort 22 -ErrorAction SilentlyContinue | Select-Object OwningProcess,State)
[ordered]@{
    created_at=[DateTime]::UtcNow.ToString('o');scope='read-only network/configuration; no UI, no model inference';
    client='Windows GTX1650';endpoints=$endpoints;
    relay_task=[ordered]@{present=[bool]$relay;state=if($relay){[string]$relay.State}else{'missing'};enabled=if($relay){$relay.Settings.Enabled}else{$false};last_result=if($relayInfo){$relayInfo.LastTaskResult}else{$null}};
    relay_processes=$relayProcesses;server_ssh_connections=$sshConnections
} | ConvertTo-Json -Depth 8 -Compress
