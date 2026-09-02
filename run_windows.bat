@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Reuse one environment across Stock Analyser versions.
set "APP_HOME=%LOCALAPPDATA%\StockAnalyser"
set "VENV_DIR=%APP_HOME%\venv"
set "HASH_FILE=%APP_HOME%\requirements.sha256"

if not exist "%APP_HOME%" mkdir "%APP_HOME%"

set "NEW_ENV=0"
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [Stock Analyser] First-time Python environment setup...
    py -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo.
        echo Failed to create the Python environment.
        echo Make sure Python is installed and the ^"py^" launcher is available.
        pause
        exit /b 1
    )
    set "NEW_ENV=1"
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo Failed to activate %VENV_DIR%
    pause
    exit /b 1
)

for /f "usebackq delims=" %%H in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath 'requirements.txt').Hash"`) do set "REQ_HASH=%%H"

set "OLD_HASH="
if exist "%HASH_FILE%" set /p OLD_HASH=<"%HASH_FILE%"

if "%NEW_ENV%"=="1" goto INSTALL_DEPS
if /I "%REQ_HASH%"=="%OLD_HASH%" goto START_APP

:INSTALL_DEPS
echo [Stock Analyser] Dependencies are new or changed. Installing once...
python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency installation failed. The existing environment has been kept.
    pause
    exit /b 1
)
>"%HASH_FILE%" echo %REQ_HASH%
echo [Stock Analyser] Dependency environment is ready.

:START_APP
echo [Stock Analyser] Starting app using cached environment...
python -m streamlit run app.py
