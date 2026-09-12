# launcher.ps1 - start the DSES EVE modem application from this repo's own conda env.
# Invoked by the desktop shortcut (install-shortcut.ps1) or launcher.bat.
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$env = Join-Path $here '.conda'
$py = Join-Path $env 'python.exe'
if (-not (Test-Path $py)) {
    [System.Windows.Forms.MessageBox] | Out-Null
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show("No project environment at $env. Create it with:`nconda env create --prefix .conda -f environment.yml", 'DSES EVE modem') | Out-Null
    exit 1
}
# numpy's MKL and UHD delay-load from the env's Library\bin: it must be on PATH (this is
# what `conda activate` does; without it python exits 127 on the first numpy.linalg call).
$env:PATH = (Join-Path $env 'Library\bin') + ';' + (Join-Path $env 'Scripts') + ';' + $env + ';' + $env:PATH
$env:PYQTGRAPH_QT_LIB = 'PySide6'
$env:CONDA_PREFIX = $env
$log = Join-Path $env:LOCALAPPDATA 'DSES\EVE_Modem\app.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
Set-Location $here
& $py (Join-Path $here 'eve_app.py') *>> $log
exit $LASTEXITCODE
