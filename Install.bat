@echo off
rem One-time setup: Python, packages, Xray/Tor, Desktop + Start menu shortcuts.
if not exist "%~dp0scripts\install.ps1" goto notextracted
pushd "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
popd
goto :eof

:notextracted
echo.
echo   Juggler is still inside the ZIP file.
echo.
echo   1. Close this window.
echo   2. Right-click the Juggler ZIP file and choose "Extract All...".
echo   3. Open the extracted Juggler folder and double-click Install.bat again.
echo.
pause
