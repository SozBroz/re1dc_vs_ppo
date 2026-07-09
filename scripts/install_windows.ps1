# One-time / repeat setup for a Windows training box (workhorse1, workhorse2, pking)
param(
    [string]$RepoUrl = "https://github.com/SozBroz/re1dc_vs_ppo.git",
    [string]$Root = "D:\re1_rl"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $Root | Out-Null
Set-Location $Root

if (-not (Test-Path ".git")) {
    git clone $RepoUrl .
} else {
    git pull origin master
}

if (-not (Test-Path "venv\Scripts\python.exe")) {
    python -m venv venv
}

& .\venv\Scripts\pip.exe install -U pip
& .\venv\Scripts\pip.exe install -r requirements.txt

Write-Host "OK: $Root ready. Copy roms/, tools/BizHawk, states/*.State locally (not in git)."
Write-Host "See docs/fleet_setup.md for learner vs worker launch."
