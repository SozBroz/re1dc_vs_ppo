$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\sshuser\re1_rl'
$taskName = 'RE1_almanac_learner'
$launcher = Join-Path $repo 'fleet\local\run_distributed_learner_wh2_25.cmd'

Set-Location $repo
schtasks /End /TN $taskName 2>$null | Out-Null
schtasks /Delete /TN $taskName /F 2>$null | Out-Null
schtasks /Create /TN $taskName /TR $launcher /SC ONCE /ST 00:00 /RL HIGHEST /IT /F
if ($LASTEXITCODE -ne 0) {
  throw "failed to create interactive WH2 task ($LASTEXITCODE)"
}
schtasks /Run /TN $taskName
if ($LASTEXITCODE -ne 0) {
  throw "failed to run interactive WH2 task ($LASTEXITCODE)"
}
Write-Output 'WH2_INTERACTIVE_STARTED'
