@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set /a ERRORS=0
set /a WARNINGS=0
set "PYTHON_CMD="
set "VENV_PY=.venv\Scripts\python.exe"

echo [TJproxy Doctor] Checking local environment...
echo.

where pwsh.exe >nul 2>nul
if errorlevel 1 goto no_pwsh
call :get_pwsh_version
call :ok "PowerShell 7 found: !PWSH_VERSION!"
goto after_pwsh
:no_pwsh
call :fail "PowerShell 7 not found. Install: https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows"
:after_pwsh

call :find_python
if errorlevel 1 goto no_python
call :get_python_version
call :ok "Python 3.11+ found: !PYTHON_VERSION!"
goto after_python
:no_python
call :fail "Python 3.11+ not found. Install: https://www.python.org/downloads/windows/"
:after_python

call :require_file "agent_cli.py"
call :require_file "server\main.py"
call :require_file "server\requirements.txt"
call :require_file "agent.toml"

if exist "agent.policy.toml" goto policy_found
call :warn "agent.policy.toml missing; built-in policy may be used"
goto after_policy
:policy_found
call :ok "agent.policy.toml found"
:after_policy

call :require_file "extension\manifest.json"

if exist "%VENV_PY%" goto have_venv
call :fail ".venv missing; run start.bat to create it"
goto after_venv

:have_venv
call :ok ".venv found"

"%VENV_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto bad_venv_python
call :ok ".venv Python version check passed"
goto after_venv_python
:bad_venv_python
call :fail ".venv Python is older than 3.11"
goto after_venv
:after_venv_python

"%VENV_PY%" -c "import websockets, requests, textual, pytest, pytest_asyncio" >nul 2>nul
if errorlevel 1 goto missing_packages
call :ok ".venv packages found"
goto after_packages
:missing_packages
call :fail ".venv is missing one or more packages from server\requirements.txt"
:after_packages

"%VENV_PY%" -m compileall tjproxy_agent server -q >nul 2>nul
if errorlevel 1 goto compile_failed
call :ok "Python compile check passed"
goto after_compile
:compile_failed
call :fail "Python compile check failed"
:after_compile

"%VENV_PY%" -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/', timeout=2).read()" >nul 2>nul
if errorlevel 1 goto service_unreachable
call :ok "TJproxy service is reachable at http://localhost:8765/"
goto after_service
:service_unreachable
call :warn "TJproxy service is not currently reachable at http://localhost:8765/"
:after_service

:after_venv

echo.
echo [TJproxy Doctor] Summary: !ERRORS! error(s), !WARNINGS! warning(s).
if !ERRORS!==0 goto doctor_success
echo [TJproxy Doctor] Fix the errors above before running TJproxy.
exit /b 1

:doctor_success
echo [TJproxy Doctor] Environment checks completed.
exit /b 0

:find_python
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto find_python_launcher
set "PYTHON_CMD=python"
exit /b 0
:find_python_launcher
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1
set "PYTHON_CMD=py -3"
exit /b 0

:get_pwsh_version
for /f "delims=" %%V in ('pwsh.exe -NoLogo -NoProfile -Command "$PSVersionTable.PSVersion.ToString()"') do set "PWSH_VERSION=%%V"
exit /b 0

:get_python_version
for /f "delims=" %%V in ('!PYTHON_CMD! -c "import sys; v=sys.version_info; print(str(v.major)+'.'+str(v.minor)+'.'+str(v.micro))"') do set "PYTHON_VERSION=%%V"
exit /b 0

:require_file
if exist "%~1" goto require_file_ok
call :fail "%~1 missing"
exit /b 0
:require_file_ok
call :ok "%~1 found"
exit /b 0

:ok
echo [OK] %~1
exit /b 0

:warn
set /a WARNINGS+=1
echo [WARN] %~1
exit /b 0

:fail
set /a ERRORS+=1
echo [FAIL] %~1
exit /b 0
