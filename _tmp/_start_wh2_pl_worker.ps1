param(
  [int]$DelaySec = 0
)
$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\sshuser\re1_rl'
Set-Location $repo

if ($DelaySec -gt 0) {
  Write-Output ("delay ${DelaySec}s before WH2 worker start")
  Start-Sleep -Seconds $DelaySec
}

$tn = 'RE1_WH2_PlannerLoyalWorker'
$launcher = Join-Path $repo 'fleet\local\run_distributed_worker_workhorse2_planner_loyal.cmd'
$q = schtasks /Query /TN $tn 2>$null
if ($LASTEXITCODE -ne 0) {
  schtasks /Create /TN $tn /TR $launcher /SC ONCE /ST 00:00 /RL HIGHEST /IT /F | Out-Host
} else {
  schtasks /Change /TN $tn /TR $launcher /RL HIGHEST | Out-Host
}
schtasks /Run /TN $tn | Out-Host
Write-Output 'WH2_PL_WORKER_SCHEDULED'
