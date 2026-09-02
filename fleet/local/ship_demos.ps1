# Ship freshly recorded human demos (data/demos/planner_loyal/*.npz) to the WH3 learner.
# Commit -> push -> git pull on WH3. The learner hot-reloads the demo dir
# (RE1_BC_RELOAD_EVERY train calls), so no restart is needed.
$ErrorActionPreference = 'Stop'
Set-Location 'D:\re1_rl'
$demoDir = 'data\demos\planner_loyal'
$files = Get-ChildItem -Path $demoDir -Filter '*.npz' -ErrorAction SilentlyContinue
if (-not $files) { Write-Host "no demos under $demoDir"; exit 1 }
git add -- "$demoDir/*.npz"
$staged = git diff --cached --name-only -- $demoDir
if (-not $staged) { Write-Host 'no new demos to ship'; } else {
  git commit -q -m "Add human pl79->80 demos ($(@($staged).Count) file(s))."
  git push -q origin (git branch --show-current)
}
$sha = git rev-parse --short HEAD
$remote = ssh.exe -o ConnectTimeout=15 -o BatchMode=yes sshuser@192.168.0.229 "cd /d C:\Users\sshuser\re1_rl && git pull -q && git rev-parse --short HEAD && dir /b data\demos\planner_loyal"
Write-Host "pking=$sha"
Write-Host ($remote -join "`n")
