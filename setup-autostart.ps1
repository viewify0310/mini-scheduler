# ─────────────────────────────────────────────────────────────────
#  setup-autostart.ps1 — make Mini Scheduler launch when you log in.
#
#  Creates a shortcut in your Windows Startup folder that runs
#  mini_scheduler.py with pythonw.exe (no console window).
#
#  Usage (from this folder):
#     powershell -ExecutionPolicy Bypass -File .\setup-autostart.ps1
#     powershell -ExecutionPolicy Bypass -File .\setup-autostart.ps1 -Remove
#
#  Re-run it any time you move this folder or change Python install.
# ─────────────────────────────────────────────────────────────────
param([switch]$Remove)

$ErrorActionPreference = 'Stop'

$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$appScript  = Join-Path $scriptDir 'mini_scheduler.py'
$startupDir = [Environment]::GetFolderPath('Startup')
$shortcut   = Join-Path $startupDir 'Mini Scheduler.lnk'

if ($Remove) {
    if (Test-Path $shortcut) {
        Remove-Item $shortcut
        Write-Host "Removed autostart shortcut: $shortcut"
    } else {
        Write-Host "No autostart shortcut found at: $shortcut"
    }
    return
}

if (-not (Test-Path $appScript)) {
    throw "Cannot find mini_scheduler.py next to this script ($appScript)."
}

# Locate the interpreter, then prefer its windowless pythonw.exe.
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "python.exe is not on PATH." }
$realExe = (& python -c "import sys; print(sys.executable)").Trim()
$pythonw = Join-Path (Split-Path -Parent $realExe) 'pythonw.exe'
if (-not (Test-Path $pythonw)) {
    Write-Warning "pythonw.exe not found; falling back to python.exe (a console window will show)."
    $pythonw = $realExe
}

# Confirm the one dependency is installed for that interpreter.
& python -c "import customtkinter" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "customtkinter is not installed for $realExe`nRun:  python -m pip install customtkinter"
}

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($shortcut)
$sc.TargetPath       = $pythonw
$sc.Arguments        = '"' + $appScript + '"'
$sc.WorkingDirectory = $scriptDir
$sc.WindowStyle      = 1
$sc.Description      = 'Mini Scheduler - desktop task scheduler'
$sc.Save()

Write-Host "Autostart shortcut created:"
Write-Host "  $shortcut"
Write-Host "  target : $pythonw"
Write-Host "  args   : `"$appScript`""
Write-Host ""
Write-Host "Mini Scheduler will now start automatically when you log in."
