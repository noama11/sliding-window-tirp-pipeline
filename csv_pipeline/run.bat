@echo off
REM One-click launcher for the self-contained CSV STROKE TIRP pipeline.
REM Double-click this file, or run it from a terminal. It runs every K/Y window
REM in config.json, producing _YES_patterns and _NO_patterns per window.
REM
REM Other uses:
REM     python run.py --list-windows     show the window plan
REM     python run.py --window 2015      run just the window with k_start=2015

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo Python was not found on PATH. Install Python 3.8+ from https://www.python.org/downloads/
    echo and make sure "Add python.exe to PATH" is checked.
    echo.
    pause
    exit /b 1
)

python run.py
echo.
pause
