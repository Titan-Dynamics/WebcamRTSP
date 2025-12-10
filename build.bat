@echo off
REM Build script for Webcam RTSP Streamer (Single-file EXE)
REM
REM Prerequisites:
REM   1. Python with pip installed
REM   2. ffmpeg.exe in src/ folder (download from https://ffmpeg.org/download.html)
REM   3. mediamtx.exe in src/ folder (download from https://github.com/bluenviron/mediamtx/releases)
REM   4. mediamtx.yml in src/ folder (from mediamtx release)

echo === Webcam RTSP Streamer Build Script ===
echo.

REM Check for required files
if not exist "src\ffmpeg.exe" (
    echo ERROR: src\ffmpeg.exe not found!
    echo Download from: https://ffmpeg.org/download.html
    echo Get the "essentials" build and extract ffmpeg.exe to src\
    pause
    exit /b 1
)

if not exist "src\mediamtx.exe" (
    echo ERROR: src\mediamtx.exe not found!
    echo Download from: https://github.com/bluenviron/mediamtx/releases
    echo Extract mediamtx.exe to src\
    pause
    exit /b 1
)

if not exist "src\mediamtx.yml" (
    echo ERROR: src\mediamtx.yml not found!
    echo Download from: https://github.com/bluenviron/mediamtx/releases
    echo Extract mediamtx.yml to src\
    pause
    exit /b 1
)

REM Install PyInstaller if needed
echo Installing/updating PyInstaller...
pip install --upgrade pyinstaller

echo.
echo Building single-file executable...
pyinstaller --clean build.spec

echo.
if exist "dist\WebcamRTSP.exe" (
    echo === BUILD SUCCESSFUL ===
    echo Output: dist\WebcamRTSP.exe
    echo.
    for %%A in (dist\WebcamRTSP.exe) do echo Size: %%~zA bytes
) else (
    echo === BUILD FAILED ===
)

echo.
pause
