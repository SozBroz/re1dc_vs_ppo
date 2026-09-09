$ErrorActionPreference = 'Stop'
$req = 'C:\Users\sshuser\re1_rl\_tmp\pl134_muse_next_request.json'
$raw = 'C:\Users\sshuser\re1_rl\_tmp\pl134_muse_next_raw.json'
Write-Host '=== muse health ==='
try {
  $h = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 5
  Write-Host ("HEALTH " + $h.StatusCode)
} catch {
  Write-Host ("HEALTH FAIL " + $_.Exception.Message)
  exit 2
}
if (-not (Test-Path $req)) { Write-Host "missing req"; exit 3 }
Write-Host ("req_bytes=" + (Get-Item $req).Length)
Write-Host '=== POST chat/completions (may take minutes) ==='
& curl.exe -sS --fail --max-time 900 `
  -H 'Content-Type: application/json' `
  -d "@$req" `
  http://127.0.0.1:8000/v1/chat/completions `
  -o $raw
$code = $LASTEXITCODE
Write-Host ("curl_exit=" + $code)
if ($code -ne 0) {
  Get-Content 'C:\Users\sshuser\re1_rl\data\logs\muse_llama_server.out.log' -Tail 40 -ErrorAction SilentlyContinue
  exit $code
}
Write-Host ("raw_bytes=" + (Get-Item $raw).Length)
Write-Host 'MUSE_CALL_OK'
