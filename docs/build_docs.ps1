# Build the DSES EVE Modem Design and ICD PDF from its Markdown source.
# Uses the DSES house-style generator from the Workbench repo (build_doc.py:
# Minion/Myriad/Source Code Pro, teal banner headings, cover, TOC, Acrobat
# Distiller PDF with embedded fonts). Run from anywhere:
#     powershell -File docs\build_docs.ps1
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$wb   = 'C:\Users\rick\Documents\DSES\Science\DSES_Workbench'
$py   = Join-Path $wb '.conda\python.exe'
$gen  = Join-Path $wb 'build_doc.py'
$logo = Join-Path $wb 'reports\assets\DSES_Logo_Compact_Teal.png'

python (Join-Path $here 'make_figures.py')

& $py $gen (Join-Path $here 'DSES_EVE_Modem_Design_and_ICD.md') `
    --pdf   (Join-Path $here 'DSES_EVE_Modem_Design_and_ICD.pdf') `
    --docx  (Join-Path $here 'DSES_EVE_Modem_Design_and_ICD.docx') `
    --title 'Earth-Venus-Earth Modem' `
    --subtitle 'Design Description and Interface Control Document' `
    --version 'Rev A - DRAFT' `
    --header-logo $logo --force
