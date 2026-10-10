@echo off
REM Build DataBizex as a Windows desktop app: dist\DataBizex\DataBizex.exe
REM Steps: create/activate .venv, install requirements, run tests, package with PyInstaller.
REM Optional: build_windows.bat --onefile  also builds a single-file dist\DataBizex.exe
REM (slower start: it unpacks to %TEMP% on every launch; the folder build stays the default).
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating virtual environment .venv ...
    py -3.11 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 goto :fail
) else (
    echo [1/4] Using existing virtual environment .venv
)
call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :fail

echo [2/4] Installing requirements ...
python -m pip install --upgrade pip --timeout 300 --retries 5
python -m pip install -r requirements.txt --timeout 300 --retries 5
if errorlevel 1 goto :fail

echo [3/4] Running tests ...
python -m pytest
if errorlevel 1 (
    echo Tests failed - executable not built.
    goto :fail
)

echo [4/4] Packaging with PyInstaller ...
python -m PyInstaller --noconfirm --clean --onedir --windowed --name DataBizex ^
    --add-data "sample_data;sample_data" ^
    --add-data "powerbi;powerbi" ^
    --add-data "rules;rules" ^
    --exclude-module tkinter ^
    run_databizex.py
if errorlevel 1 goto :fail

if not exist "dist\DataBizex\DataBizex.exe" goto :fail
echo.
echo Build complete: %CD%\dist\DataBizex\DataBizex.exe

if /i "%~1"=="--onefile" (
    echo.
    echo [extra] Packaging single-file executable ...
    python -m PyInstaller --noconfirm --clean --onefile --windowed --name DataBizex ^
        --workpath build\onefile ^
        --add-data "sample_data;sample_data" ^
        --add-data "powerbi;powerbi" ^
        --add-data "rules;rules" ^
        --exclude-module tkinter ^
        run_databizex.py
    if errorlevel 1 goto :fail
    if not exist "dist\DataBizex.exe" goto :fail
    echo Single-file build complete: %CD%\dist\DataBizex.exe
)
endlocal
exit /b 0

:fail
echo.
echo Build FAILED. See the messages above.
endlocal
exit /b 1
