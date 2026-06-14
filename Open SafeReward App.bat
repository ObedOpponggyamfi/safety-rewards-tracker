@echo off
setlocal EnableExtensions

cd /d "%~dp0"
chcp 65001 >nul
title SafeReward App

set "APP_PORT=8090"
if not "%PORT%"=="" set "APP_PORT=%PORT%"

echo.
echo SafeReward App
echo ----------------
echo Project: %CD%
echo URL:     http://localhost:%APP_PORT%/
echo.

netstat -ano -p tcp | findstr /R /C:":%APP_PORT% .*LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo A server is already running on port %APP_PORT%.
    echo Opening the existing app in your browser...
    start "" "http://localhost:%APP_PORT%/"
    exit /b 0
)

where py >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>nul
    if not errorlevel 1 (
        set "PY_CMD=python"
    ) else (
        echo Python 3 was not found on PATH.
        echo Install Python 3 or add it to PATH, then run this file again.
        echo.
        pause
        exit /b 1
    )
)

echo Starting SafeReward...
echo Leave this window open while using the app.
echo Press Ctrl+C here when you want to stop the server.
echo.

%PY_CMD% app.py
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo SafeReward stopped with exit code %EXIT_CODE%.
    echo.
    pause
)

exit /b %EXIT_CODE%
