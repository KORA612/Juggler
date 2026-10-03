@echo off
setlocal
title Juggler
rem Starts Juggler. The Desktop / Start menu shortcuts point here.
pushd "%~dp0"
chcp 65001 >nul

set "PY="
py -3 -c "import flask, qrcode" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import flask, qrcode" >nul 2>nul && set "PY=python"
if not defined PY goto setup
if not exist "bin\xray.exe" goto setup

%PY% run.py %*
echo.
echo Juggler stopped. You can close this window.
pause >nul
goto :eof

:setup
echo Juggler is not set up yet, starting the installer...
call "%~dp0Install.bat"
