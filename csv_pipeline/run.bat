@echo off
REM One-click launcher for the self-contained CSV TIRP pipeline.
REM Double-click this file, or run it from a terminal. It runs the default
REM cohort (patients\cohort20.txt) over all windows in config.json.
REM
REM To use your own patient file / window, run run.py directly, e.g.:
REM     python run.py --patients myids.txt --all-windows
REM     python run.py --patients myids.txt --k-start 2022 --k-end 2024

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

python run.py --patients cohort20.txt --all-windows
echo.
pause
