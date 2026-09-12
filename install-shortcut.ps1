# install-shortcut.ps1 - create a desktop shortcut to the DSES EVE modem application.
# Run once per Windows machine (from anywhere):  powershell -File install-shortcut.ps1
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$name = 'DSES EVE Modem'
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop "$name.lnk"
$launcher = Join-Path $here 'launcher.ps1'
$icon = Join-Path $here 'icons\eve_modem.ico'
if (-not (Test-Path $icon)) {
    $py = Join-Path $here '.conda\python.exe'
    if (Test-Path $py) {
        $env:PATH = (Join-Path $here '.conda\Library\bin') + ';' + $env:PATH
        & $py (Join-Path $here 'icons\generate-icon.py')
    }
}
$ps = (Get-Command powershell.exe).Source
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = $ps
$s.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`""
$s.WorkingDirectory = $here
$s.Description = 'DSES Earth-Venus-Earth modem: bench, EME, and Venus sessions'
$s.WindowStyle = 7
if (Test-Path $icon) { $s.IconLocation = "$icon, 0" }
$s.Save()
Write-Host "Created: $lnk" -ForegroundColor Green
