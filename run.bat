@echo off
REM Runs YouTube Downloader from source (development mode).
REM Double-click this file, or run it from a terminal.

cd /d "%~dp0"
setlocal

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_LAUNCH=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY_LAUNCH=python"
    ) else (
        echo.
        echo Python wasn't found on this computer.
        echo Install Python 3.10 or newer from https://www.python.org/downloads/
        echo   ^(make sure "Add python.exe to PATH" is checked during install^),
        echo then run this file again.
        echo.
        pause
        exit /b 1
    )
)

if not exist ".venv" (
    echo Setting up a virtual environment ^(first run only^)...
    %PY_LAUNCH% -m venv .venv
    if errorlevel 1 (
        echo.
        echo Failed to create the virtual environment. See the error above.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

echo Checking dependencies ^(this only installs anything the first time^)...
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install dependencies. See the error above.
    pause
    exit /b 1
)

if not exist "ffmpeg.exe" (
    echo.
    echo Note: ffmpeg.exe not found next to this file -- the app will still
    echo open, but downloads that need it will fail until it's added.
    echo.
)

echo.
echo Starting YouTube Downloader...
python main.py

endlocal
