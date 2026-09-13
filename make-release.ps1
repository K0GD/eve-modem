# make-release.ps1 - build dist\eve-modem-<version>.zip (+ .sha256) with this repo's env.
# The work is in tools\make_release.py (one cross-platform builder; forward-slash zip).
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$env = Join-Path $here '.conda'
$env:PATH = (Join-Path $env 'Library\bin') + ';' + $env:PATH
& (Join-Path $env 'python.exe') (Join-Path $here 'tools\make_release.py') @args
exit $LASTEXITCODE
