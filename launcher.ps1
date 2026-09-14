# launcher.ps1 - start the DSES EVE modem application (Windows).
# Invoked by the desktop shortcut (install-shortcut.ps1) or launcher.bat.
# Python, in order: a project env beside the program (.conda\), else EVE_PYTHON (a
# python.exe), else RADIOCONDA_ROOT / CONDA_PREFIX, else radioconda in its usual places.
# The env needs gnuradio + uhd + PySide6 + pyqtgraph + numpy/scipy + astropy + jplephem and
# the pip extras in environment.yml (galois, sigmf, hidapi, pymupdf, pyserial); a
# radioconda gains them with:  <radioconda>\Scripts\pip.exe install galois sigmf hidapi pymupdf pyserial astropy jplephem
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot

function Usable([string]$prefix) {
    if (-not $prefix) { return $false }
    $py = Join-Path $prefix 'python.exe'
    if (-not (Test-Path $py)) { return $false }
    $save = $env:PATH
    $env:PATH = (Join-Path $prefix 'Library\bin') + ';' + $env:PATH
    & $py -c 'import gnuradio, PySide6' 2>$null | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    $env:PATH = $save
    return $ok
}

$prefix = $null
$local = Join-Path $here '.conda'
if (Test-Path (Join-Path $local 'python.exe')) { $prefix = $local }
elseif ($env:EVE_PYTHON -and (Test-Path $env:EVE_PYTHON)) { $prefix = Split-Path $env:EVE_PYTHON -Parent }
else {
    foreach ($c in @($env:RADIOCONDA_ROOT, $env:CONDA_PREFIX, 'C:\ProgramData\radioconda',
                     (Join-Path $env:USERPROFILE 'radioconda'), (Join-Path $env:LOCALAPPDATA 'radioconda'))) {
        if (Usable $c) { $prefix = $c; break }
    }
}
if (-not $prefix) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "No Python with GNU Radio and PySide6 found.`n`nEither create the program's own environment here:`n  conda env create --prefix .conda -f environment.yml`nor install radioconda and add the extras:`n  pip install galois sigmf hidapi pymupdf pyserial astropy jplephem`n(or set EVE_PYTHON to a python.exe that has them).",
        'DSES EVE modem') | Out-Null
    exit 1
}
$py = Join-Path $prefix 'python.exe'
# numpy's MKL and UHD delay-load from the env's Library\bin: it must be on PATH (this is
# what `conda activate` does; without it python exits 127 on the first numpy.linalg call).
$env:PATH = (Join-Path $prefix 'Library\bin') + ';' + (Join-Path $prefix 'Scripts') + ';' + $prefix + ';' + $env:PATH
# The env may have GNU Radio but not the modem's extras: say which, instead of dying in the log.
$missing = & $py -c "import importlib; print(' '.join(m for m in ['galois','sigmf','hid','pymupdf','serial','astropy','jplephem','pyqtgraph','scipy','matplotlib'] if not importlib.util.find_spec(m)))" 2>$null
if ($missing) {
    $pipmods = ($missing -replace '\bhid\b', 'hidapi') -replace '\bserial\b', 'pyserial'
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "The Python at $prefix lacks: $missing`n`nAdd them with:`n  $(Join-Path $prefix 'Scripts\pip.exe') install $pipmods",
        'DSES EVE modem') | Out-Null
    exit 1
}
$env:PYQTGRAPH_QT_LIB = 'PySide6'
$env:PYTHONUNBUFFERED = '1'
$env:CONDA_PREFIX = $prefix
$log = Join-Path $env:LOCALAPPDATA 'DSES\EVE_Modem\app.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
Set-Location $here
& cmd /c """$py"" ""$(Join-Path $here 'eve_app.py')"" >> ""$log"" 2>&1"
exit $LASTEXITCODE
