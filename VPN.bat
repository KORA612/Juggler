@echo off
setlocal
title Juggler
rem Double-click to start. Works from any folder (pushd also handles \\wsl.localhost paths).
pushd "%~dp0"
chcp 65001 >nul

set "PY=py -3"
%PY% --version >nul 2>nul || set "PY=python"
%PY% --version >nul 2>nul || (
  echo Python 3 was not found. Install it from https://www.python.org/downloads/ and try again.
  pause
  exit /b 1
)

%PY% -c "import flask, qrcode" >nul 2>nul || (
  echo Installing Flask and qrcode...
  %PY% -m pip install --user --disable-pip-version-check -q -r requirements.txt
)

%PY% run.py %*
popd
echo.
pause
