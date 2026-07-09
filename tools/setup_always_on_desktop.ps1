#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Keep an interactive Windows desktop alive for BizHawk without RDP or a monitor.

.DESCRIPTION
  BizHawk/EmuHawk needs a real user desktop session (not OpenSSH Session 0).
  This script configures:
    1) Autologon for the training account (console session after reboot)
    2) Never sleep / never lock on idle
    3) Optional virtual display driver install notes (or use an HDMI dummy plug)
    4) At-logon Scheduled Task that starts the RE1 worker/learner in that desktop

  After reboot, the machine logs into the desktop by itself. You can SSH for
  admin/git without needing a second interactive session for BizHawk.

.PARAMETER Username
  Local account that owns the desktop (default: sshuser).

.PARAMETER Password
  Password for autologon. Prefer -SecurePassword or interactive prompt.

.PARAMETER Role
  worker | learner | none — which logon task to install.

.PARAMETER RepoRoot
  Path to re1_rl on this box.

.PARAMETER SkipAutologon
  Only power/lock/task changes.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\setup_always_on_desktop.ps1 -Role worker -RepoRoot D:\re1_rl
#>
param(
    [string]$Username = "sshuser",
    [string]$Password = "",
    [SecureString]$SecurePassword,
    [ValidateSet("worker", "learner", "none")]
    [string]$Role = "none",
    [string]$RepoRoot = "",
    [switch]$SkipAutologon,
    [switch]$InstallVirtualDisplay
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) { Write-Host "[always-on] $msg" -ForegroundColor Cyan }

# --- resolve password ---
if (-not $SkipAutologon) {
    if ($SecurePassword) {
        $BSTR = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePassword)
        $Password = [Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
    }
    elseif (-not $Password) {
        $sec = Read-Host "Password for $Username (autologon)" -AsSecureString
        $BSTR = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        $Password = [Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
    }
    if (-not $Password) { throw "Password required for autologon (or pass -SkipAutologon)" }
}

# --- detect repo ---
if (-not $RepoRoot) {
    if (Test-Path "D:\re1_rl") { $RepoRoot = "D:\re1_rl" }
    elseif (Test-Path "C:\Users\sshuser\re1_rl") { $RepoRoot = "C:\Users\sshuser\re1_rl" }
    else { throw "Pass -RepoRoot (neither D:\re1_rl nor C:\Users\sshuser\re1_rl found)" }
}
$RepoRoot = (Resolve-Path $RepoRoot).Path
Write-Step "RepoRoot=$RepoRoot Role=$Role User=$Username"

# --- 1) Autologon (Winlogon) ---
# Stores password in registry (acceptable on dedicated LAN training boxes).
# For stronger storage, install Sysinternals Autologon.exe later.
if (-not $SkipAutologon) {
    Write-Step "Configuring Winlogon autologon for $Username"
    $winlogon = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
    Set-ItemProperty -Path $winlogon -Name "AutoAdminLogon" -Value "1" -Type String
    Set-ItemProperty -Path $winlogon -Name "DefaultUserName" -Value $Username -Type String
    Set-ItemProperty -Path $winlogon -Name "DefaultPassword" -Value $Password -Type String
    # Clear domain for local account
    Set-ItemProperty -Path $winlogon -Name "DefaultDomainName" -Value $env:COMPUTERNAME -Type String
    # Don't count down / don't force password expiry UI
    Set-ItemProperty -Path $winlogon -Name "AutoLogonCount" -Value 0xFFFFFFFF -Type DWord -ErrorAction SilentlyContinue
    Write-Step "Autologon enabled (reboot required to take effect)"
}

# --- 2) Power / lock / screen ---
Write-Step "Disabling sleep, hibernate, monitor-off, lock on idle"
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change standby-timeout-dc 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-dc 0 | Out-Null
powercfg /change monitor-timeout-ac 0 | Out-Null
powercfg /change monitor-timeout-dc 0 | Out-Null
powercfg /hibernate off 2>$null | Out-Null

# No lock screen after screensaver; disable screensaver
$desk = "HKCU:\Control Panel\Desktop"
Set-ItemProperty -Path $desk -Name "ScreenSaveActive" -Value "0" -Type String
Set-ItemProperty -Path $desk -Name "ScreenSaveTimeOut" -Value "0" -Type String
# Also set for the target user if we're elevating from another account — best-effort via HKU after load
try {
    $sid = (New-Object System.Security.Principal.NTAccount($Username)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    $userHive = "Registry::HKEY_USERS\$sid\Control Panel\Desktop"
    if (Test-Path $userHive) {
        Set-ItemProperty -Path $userHive -Name "ScreenSaveActive" -Value "0" -Type String
        Set-ItemProperty -Path $userHive -Name "ScreenSaveTimeOut" -Value "0" -Type String
    }
} catch { Write-Step "Could not set HKU screensaver for $Username (ok if first logon pending)" }

# Don't require Ctrl+Alt+Del; don't lock on resume
$winlogon = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
Set-ItemProperty -Path $winlogon -Name "DisableCAD" -Value 1 -Type DWord -ErrorAction SilentlyContinue
$sys = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization"
New-Item -Path $sys -Force | Out-Null
Set-ItemProperty -Path $sys -Name "NoLockScreen" -Value 1 -Type DWord

# Keep console session when RDP disconnects (optional policy)
# Users should prefer NOT RDP'ing the training session; use SSH for admin.

# --- 3) Virtual display ---
# Software driver is optional; HDMI dummy plug is more reliable for BizHawk.
$vddNote = Join-Path $RepoRoot "docs\always_on_desktop.md"
if ($InstallVirtualDisplay) {
    Write-Step "InstallVirtualDisplay requested - see docs for Virtual Display Driver / HDMI dummy"
    Write-Host ""
    Write-Host "  RECOMMENDED: plug an HDMI/DP dummy dongle."
    Write-Host "  SOFTWARE: Virtual Display Driver (IddCx) from"
    Write-Host "    https://github.com/VirtualDisplay/Virtual-Display-Driver/releases"
    Write-Host "  then set 1920x1080 in Display Settings after reboot."
    Write-Host ""
}

# --- 4) Logon scheduled task (runs IN the interactive desktop) ---
if ($Role -ne "none") {
    $taskName = "RE1_" + $Role + "_at_logon"
    if ($Role -eq "worker") {
        $starter = Join-Path $RepoRoot "fleet\local\start_worker_at_logon.cmd"
    } elseif ($Role -eq "learner") {
        $starter = Join-Path $RepoRoot "fleet\local\start_learner_at_logon.cmd"
    } else {
        throw "Unknown Role=$Role"
    }
    if (-not (Test-Path $starter)) {
        throw "Missing starter script: $starter"
    }

    Write-Step "Installing Scheduled Task $taskName -> $starter"
    # Run only when user is logged on = interactive session (not Session 0)
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument ("/c `"" + $starter + "`"") -WorkingDirectory $RepoRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $Username
    # Delay so desktop/shell is up
    $trigger.Delay = "PT45S"
    $principal = New-ScheduledTaskPrincipal -UserId $Username -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Write-Step ("Task " + $taskName + " registered (AtLogOn Interactive)")
}

Write-Host ""
Write-Step "DONE. Next:"
Write-Host "  1) Prefer an HDMI dummy plug if no monitor (BizHawk loves a real EDID)."
Write-Host "  2) Reboot this machine once so autologon creates an Active console session."
Write-Host "  3) After reboot, query user  should show sshuser STATE=Active (not Disc)."
Write-Host "  4) SSH remains for git/admin; do not rely on SSH to spawn EmuHawk."
Write-Host "  5) If you RDP later, disconnect with: tscon <sessionid> /dest:console"
Write-Host "     so the desktop returns to the console instead of going Disc."
Write-Host ""
