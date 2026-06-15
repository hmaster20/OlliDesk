@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo   OlliDesk Build Script
echo ============================================
echo.

:: --- 1. Проверка зависимостей ---
echo [1/4] Checking dependencies...

where pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller not found. Install with: pip install pyinstaller
    exit /b 1
)

pip show PySide6 >nul 2>&1
if errorlevel 1 (
    echo ERROR: PySide6 not installed. Run: pip install -r requirements.txt
    exit /b 1
)

echo OK
echo.

:: --- 2. Проверка Monaco Editor ---
echo [2/4] Checking Monaco Editor...
set MONACO_DIR=ui\web_editor\vendor\monaco

if not exist "%MONACO_DIR%\vs\editor\editor.main.js" (
    echo Monaco editor not found. Downloading...
    python scripts\download_monaco.py
    if !errorlevel! neq 0 (
        echo ERROR: Failed to download Monaco Editor.
        exit /b 1
    )
) else (
    echo OK ^(already exists^)
)
echo.

:: --- 3. PyInstaller ---
echo [3/4] Building OlliDesk.exe...

pyinstaller --clean --noconfirm ollidesk.spec
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller build failed.
    exit /b 1
)
echo.

:: --- 4. Cleanup build artifacts ---
echo [4/4] Cleaning up...
if exist "build" (
    rd /s /q "build"
    echo Removed build/
)
echo.

echo ============================================
echo  Build complete!
echo  Output: dist\OlliDesk.exe
echo ============================================

pause
