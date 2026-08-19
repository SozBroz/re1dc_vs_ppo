$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\sshuser\re1_rl'
$taskName = 'RE1_almanac_learner'
$launcher = Join-Path $repo 'fleet\local\run_distributed_learner_wh2_25.cmd'

Set-Location $repo
$sessionLines = @(quser $env:USERNAME 2>$null)
foreach ($line in $sessionLines) {
  $fields = @(($line -replace '^>', '').Trim() -split '\s+')
  $stateIndex = [Array]::IndexOf($fields, 'Disc')
  if ($stateIndex -lt 1) {
    continue
  }
  $sessionId = $fields[$stateIndex - 1]
  tscon.exe $sessionId /dest:console
  if ($LASTEXITCODE -ne 0) {
    throw "failed to reattach WH2 desktop session $sessionId ($LASTEXITCODE)"
  }
  Write-Output "WH2_SESSION_REATTACHED=$sessionId"
  break
}
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
