@echo off
rem Double-click launcher for the Tempa dashboard (Windows).
rem Keep this window open while you use the dashboard - closing it stops the server.
title Tempa Dashboard
cd /d "%~dp0"

rem Prefer the py launcher (like tempa.cmd), fall back to python on PATH.
set "PY_EXE=py"
where py >nul 2>nul || set "PY_EXE=python"
where %PY_EXE% >nul 2>nul || goto :no_python

%PY_EXE% "%~dp0tempa.py" dashboard
if errorlevel 1 goto :failed
goto :eof

:no_python
echo.
echo Python 3 was not found on this machine.
echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
echo then double-click this file again.
echo.
pause
goto :eof

:failed
echo.
echo The dashboard stopped with an error - see the messages above.
echo.
pause
