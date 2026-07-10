@echo off
REM ============================================================
REM  run_plotter.bat — Double-click to launch the Plotter GUI
REM  Place this file next to plotter_gui.py
REM ============================================================

cd /d "%~dp0"
pythonw Src/plotter_gui.py 2>nul
if errorlevel 1 (
    python plotter_gui.py
    if errorlevel 1 (
        echo.
        echo ERROR: Python not found. Make sure Python is installed and added to PATH.
        echo Download from https://www.python.org/downloads/
        echo During install, check "Add Python to PATH"
        echo.
        pause
    )
)
