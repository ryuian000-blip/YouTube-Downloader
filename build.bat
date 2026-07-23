@echo off
REM Builds the standalone YouTube Downloader.exe with PyInstaller.
REM Double-click this file, or run it from a terminal.
REM
REM Expects ffmpeg.exe, ffprobe.exe, and deno.exe to already be sitting in
REM this same folder -- build.spec bundles them if present and silently
REM skips any that are missing (see README.md).

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

echo.
set MISSING=0
if not exist "ffmpeg.exe"  ( echo   - ffmpeg.exe not found  & set MISSING=1 )
if not exist "ffprobe.exe" ( echo   - ffprobe.exe not found & set MISSING=1 )
if not exist "deno.exe"    ( echo   - deno.exe not found    & set MISSING=1 )
if %MISSING%==1 (
    echo Building anyway -- the .exe will still work, but downloads that
    echo need the missing binary/binaries will fail until they're added
    echo to this folder and it's rebuilt.
    echo.
)

echo Building YouTube Downloader.exe with PyInstaller...
echo ^(this can take a couple of minutes^)
echo.
python -m PyInstaller build.spec
if errorlevel 1 (
    echo.
    echo Build failed. See the error above.
    pause
    exit /b 1
)

echo.
echo Done. The finished app is at:
echo   %cd%\dist\YouTube Downloader.exe
echo.
pause
endlocal
