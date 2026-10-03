@echo off
rem One-time setup: Python, packages, Xray/Tor, Desktop + Start menu shortcuts.
pushd "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
popd
