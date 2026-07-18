# Tail per-episode end lines from all fleet training logs.
# Matches [episode] ... from TrainingProgressTracker._on_episode_end.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File fleet\local\tail_episode_ends.ps1
#   powershell ... -File fleet\local\tail_episode_ends.ps1 -Follow
#   powershell ... -File fleet\local\tail_episode_ends.ps1 -Last 50

param(
    [switch]$Follow,
    [switch]$IncludeRollouts,
    [int]$Last = 30,
    [string]$PkingLog = 'D:\re1_rl\data\logs\worker_pking.log',
    [string]$Wh1Host = 'workhorse1',
    [string]$Wh1Log = 'D:\re1_rl\data\logs\worker_workhorse1.log',
    [string]$Wh2Host = 'workhorse2',
    [string]$Wh2Log = 'C:\Users\sshuser\re1_rl\data\logs\learner_wh2_25.log'
)

$ErrorActionPreference = 'Continue'
$episodePattern = '\[episode\]'
$rolloutPattern = '\[rollout\].*ep_rew='

function Get-MatchPatterns {
    if ($IncludeRollouts) {
        return @($episodePattern, $rolloutPattern)
    }
    return @($episodePattern)
}

function Get-LocalEpisodeLines {
    param([string]$Path, [int]$Count)
    if (-not (Test-Path $Path)) {
        return @()
    }
    $lines = Select-String -Path $Path -Pattern (Get-MatchPatterns) |
        ForEach-Object { $_.Line }
    return @($lines | Select-Object -Last $Count)
}

function Show-Recent {
    param([string]$Label, [string]$Path)
    if (-not (Test-Path $Path)) {
        Write-Host "[$Label] missing $Path" -ForegroundColor DarkYellow
        return
    }
    Write-Host "`n=== $Label (last $Last) ===" -ForegroundColor Cyan
    Get-LocalEpisodeLines -Path $Path -Count $Last | ForEach-Object { $_ }
}

function Get-RemoteEpisodeLines {
    param([string]$HostName, [string]$Path, [int]$Count)
    $exists = ssh $HostName "if exist `"$Path`" (echo exists) else (echo missing)" 2>$null
    if ($exists -notmatch 'exists') {
        Write-Host "[$HostName] missing $Path" -ForegroundColor DarkYellow
        return @()
    }
    $needle = if ($IncludeRollouts) { 'findstr /C:"[episode]" /C:"[rollout]"' } else { 'findstr /C:"[episode]"' }
    $lines = @(ssh $HostName "$needle `"$Path`"" 2>$null |
        Where-Object {
            $_ -match $episodePattern -or ($IncludeRollouts -and $_ -match $rolloutPattern)
        })
    return @($lines | Select-Object -Last $Count)
}

function Show-RemoteRecent {
    param([string]$Label, [string]$HostName, [string]$Path)
    Write-Host "`n=== $Label (last $Last) ===" -ForegroundColor Cyan
    Get-RemoteEpisodeLines -HostName $HostName -Path $Path -Count $Last | ForEach-Object { $_ }
}

function Follow-Local {
    param([string]$Label, [string]$Path)
    if (-not (Test-Path $Path)) {
        Write-Host "[$Label] missing $Path" -ForegroundColor DarkYellow
        return
    }
    Write-Host "[$Label] following $Path" -ForegroundColor Green
    Get-Content -Path $Path -Tail 0 -Wait |
        Where-Object {
            $_ -match $episodePattern -or ($IncludeRollouts -and $_ -match $rolloutPattern)
        } |
        ForEach-Object { Write-Host "[$Label] $_" }
}

function Follow-Remote {
    param([string]$Label, [string]$HostName, [string]$Path)
    Write-Host "[$Label] following via ssh $HostName" -ForegroundColor Green
    $matchExpr = if ($IncludeRollouts) {
        "`$_ -match '\[episode\]' -or `$_ -match '\[rollout\].*ep_rew='"
    } else {
        "`$_ -match '\[episode\]'"
    }
    $inner = "Get-Content -LiteralPath '$($Path -replace '''', '''''')' -Tail 0 -Wait | Where-Object { $matchExpr } | ForEach-Object { Write-Host '[$Label] ' `$_ }"
    $bytes = [System.Text.Encoding]::Unicode.GetBytes($inner)
    $encoded = [Convert]::ToBase64String($bytes)
    ssh $HostName "powershell -NoProfile -EncodedCommand $encoded"
}

if ($Follow) {
    Write-Host 'Following [episode] lines (Ctrl+C to stop)...' -ForegroundColor Cyan
    $jobs = @(
        Start-Job { param($l, $p) Follow-Local -Label $l -Path $p } -ArgumentList 'pking', $PkingLog
        Start-Job { param($l, $h, $p) Follow-Remote -Label $l -HostName $h -Path $p } -ArgumentList 'wh1', $Wh1Host, $Wh1Log
        Start-Job { param($l, $h, $p) Follow-Remote -Label $l -HostName $h -Path $p } -ArgumentList 'wh2', $Wh2Host, $Wh2Log
    )
    try {
        while ($true) {
            foreach ($job in $jobs) {
                Receive-Job $job
            }
            Start-Sleep -Milliseconds 200
        }
    }
    finally {
        $jobs | Stop-Job -Force
        $jobs | Remove-Job -Force
    }
}
else {
    Show-Recent -Label 'pking' -Path $PkingLog
    Show-RemoteRecent -Label 'wh1' -HostName $Wh1Host -Path $Wh1Log
    Show-RemoteRecent -Label 'wh2' -HostName $Wh2Host -Path $Wh2Log
    Write-Host "`nTip: add -Follow for live tail, -Last 100 for more history, -IncludeRollouts for epoch aggregates." -ForegroundColor DarkGray
}
