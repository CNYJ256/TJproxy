@echo off
setlocal EnableExtensions

cd /d "%~dp0"

echo [TJproxy] Checking PowerShell 7...
where pwsh.exe >nul 2>nul
if errorlevel 1 (
    echo [ERROR] PowerShell 7 was not found.
    echo Install it manually, then run this script again:
    echo https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows
    pause
    exit /b 1
)
echo [OK] PowerShell 7 found.

echo [TJproxy] Checking Python 3.11+...
call :find_python
if errorlevel 1 (
    echo [ERROR] Python 3.11 or newer was not found.
    echo Install it manually, then run this script again:
    echo https://www.python.org/downloads/windows/
    pause
    exit /b 1
)
for /f "delims=" %%V in ('%PYTHON_CMD% -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"') do set "PYTHON_VERSION=%%V"
echo [OK] Python %PYTHON_VERSION% found.

if exist ".venv\Scripts\python.exe" (
    echo [OK] .venv already exists; skipping creation and package install.
) else (
    echo [TJproxy] Creating .venv...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv.
        pause
        exit /b 1
    )

    echo [TJproxy] Installing Python packages...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 (
        echo [ERROR] Failed to upgrade pip.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install -r server\requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install packages from server\requirements.txt.
        pause
        exit /b 1
    )
)

echo [TJproxy] Starting Agent CLI...
".venv\Scripts\python.exe" agent_cli.py --workspace "%CD%" %*
exit /b %ERRORLEVEL%

:find_python
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    exit /b 0
)
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    exit /b 0
)
exit /b 1
