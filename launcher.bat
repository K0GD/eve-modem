@echo off
rem launcher.bat - start the DSES EVE modem application (double-click, or a shortcut target).
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0launcher.ps1"
