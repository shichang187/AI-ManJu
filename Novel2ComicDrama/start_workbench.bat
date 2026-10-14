@echo off
chcp 65001 >nul
title AI Comic Drama Workbench 0.7
cd /d "%~dp0"
if defined AIMANJU_PYTHON (
    "%AIMANJU_PYTHON%" scripts\doctor.py
    if errorlevel 1 goto done
    "%AIMANJU_PYTHON%" workbench_server.py
    goto done
)
py -3 -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
if %errorlevel% equ 0 (
    py -3 scripts\doctor.py
    if errorlevel 1 goto done
    py -3 workbench_server.py
    goto done
)
python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
if %errorlevel% equ 0 (
    python scripts\doctor.py
    if errorlevel 1 goto done
    python workbench_server.py
    goto done
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" scripts\doctor.py
    if errorlevel 1 goto done
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" workbench_server.py
    goto done
)
echo Python 3.10+ cannot execute. Install Python and add it to PATH.
:done
pause
