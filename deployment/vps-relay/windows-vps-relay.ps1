param(
  [string]$RuntimeRoot = "C:\CloudPath\runtime\vps-relay",
  [string]$VpsHost = "213.145.87.189",
  [string]$VpsUser = "cprelay",
  [ValidateRange(1,65535)][int]$RelayPort = 18100,
  [ValidateRange(1,65535)][int]$GatewayPort = 8100
)
$ErrorActionPreference = "Continue"
$ssh = Join-Path $env:WINDIR "System32\OpenSSH\ssh.exe"
$opts = @("-N", "-E", "$RuntimeRoot\ssh-error.log",
  "-i", "$RuntimeRoot\cprelay_ed25519",
  "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
  "-o", "UserKnownHostsFile=$RuntimeRoot\known_hosts",
  "-o", "StrictHostKeyChecking=yes",
  "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
  "-o", "ExitOnForwardFailure=yes",
  "-R", "127.0.0.1:${RelayPort}:127.0.0.1:${GatewayPort}", "${VpsUser}@${VpsHost}")
while ($true) {
  Add-Content "$RuntimeRoot\vps-relay.log" ("{0} start" -f (Get-Date -Format s))
  & $ssh @opts 2>> "$RuntimeRoot\vps-relay.log"
  Add-Content "$RuntimeRoot\vps-relay.log" ("{0} exit {1}" -f (Get-Date -Format s), $LASTEXITCODE)
  Start-Sleep -Seconds 10
}
