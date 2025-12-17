@echo off
REM MedicalAI - Apple Health Auto Import Watcher
REM Tento script spustí file watcher, ktorý sleduje iCloud priečinok

echo ========================================
echo MedicalAI - Apple Health Auto Import
echo ========================================
echo.

REM Prejdi do backend priečinka
cd /d "%~dp0.."

REM Aktivuj virtual environment (ak existuje)
if exist "venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
)

REM Skontroluj, či je nainštalovaný watchdog
python -c "import watchdog" 2>nul
if errorlevel 1 (
    echo ERROR: watchdog is not installed!
    echo.
    echo Please install dependencies:
    echo   pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo.
echo Starting Apple Health File Watcher...
echo Watching: %USERPROFILE%\iCloudDrive\MedicalAI\exports
echo.
echo Press Ctrl+C to stop
echo.

REM Spusti watcher
python scripts\apple_health_watcher.py

pause
