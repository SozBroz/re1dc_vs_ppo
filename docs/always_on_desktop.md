# Always-on interactive desktop (headless BizHawk)

## Problem

EmuHawk needs a **real Windows user desktop**. OpenSSH runs in **Session 0** / a non-interactive context — that is why WH1 **registered** yesterday but never connected Lua (then `EOFError`).

RDP helps only while the session is connected; disconnect → `STATE=Disc` and the same failure mode returns. No physical monitor can also yield a 0×0 display that breaks GUI apps.

## Solution (fleet standard)

| Layer | What |
|-------|------|
| **Autologon** | Console logs in as `sshuser` after every reboot — desktop stays **Active** without RDP |
| **Display** | HDMI/DP **dummy plug** (preferred) or Virtual Display Driver |
| **Power/lock** | Never sleep, never screensaver, no lock screen |
| **At-logon task** | Starts learner/worker **inside** that desktop (`LogonType Interactive`) |
| **SSH** | Still fine for git/admin — **one SSH at a time is OK**; do not spawn EmuHawk from SSH |

## One-time setup (on each box, elevated)

```powershell
cd D:\re1_rl   # or C:\Users\sshuser\re1_rl on WH2
powershell -ExecutionPolicy Bypass -File tools\setup_always_on_desktop.ps1 `
  -Username sshuser -Role worker -RepoRoot D:\re1_rl
```

WH2 learner host:

```powershell
cd C:\Users\sshuser\re1_rl
powershell -ExecutionPolicy Bypass -File tools\setup_always_on_desktop.ps1 `
  -Username sshuser -Role learner -RepoRoot C:\Users\sshuser\re1_rl
```

You will be prompted for the account password (stored in Winlogon for autologon — dedicated LAN boxes only).

Then **reboot once**. After reboot:

```bat
query user
```

Expect `sshuser` **Active** (not Disc). Check `data\logs\at_logon.log` / worker logs.

## Display without a monitor

1. **Best:** HDMI or DisplayPort **dummy EDID plug** (~$5–15). Plug it in and forget it.
2. **Software:** [Virtual Display Driver](https://github.com/VirtualDisplay/Virtual-Display-Driver/releases) (IddCx). Install, reboot, set 1920×1080.

Without one of these, headless boxes often fail GUI init even with autologon.

## BizHawk firmware paths (WH2 / no D: drive)

`config.ini` must **not** hardcode `D:\re1_rl\...\SCPH1001.BIN` on machines
without a `D:` drive. That yields a blocking **Missing Firmware!** modal:
TCP accepts, Lua never boots, Python hangs on hello.

After cloning or copying BizHawk config onto WH2:

```powershell
cd C:\Users\sshuser\re1_rl
powershell -ExecutionPolicy Bypass -File tools\patch_bizhawk_paths.ps1 `
  -RepoRoot C:\Users\sshuser\re1_rl
```

Prefer relative `./Firmware/SCPH1001.BIN` (what the patcher writes).

## If you must RDP

Disconnecting RDP parks the session as **Disc**. Return the session to console instead of leaving it disconnected:

```bat
query user
tscon <SESSION_ID> /dest:console
```

Prefer SSH for day-to-day admin so the console session stays undisturbed.

## SSH “only one machine at a time”

That constraint is fine. Autologon means you do **not** need an open RDP/SSH session for BizHawk — reboot, walk away, SSH only when you need to poke git or logs.

## Security note

Winlogon autologon stores the password in the registry. Acceptable on isolated training PCs; do not use on a shared/general-purpose workstation. Sysinternals **Autologon** can store credentials in LSA secrets if you want a slightly better variant later.
