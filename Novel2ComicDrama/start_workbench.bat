@echo off
chcp 65001 >nul
title AI Comic Drama Workbench
cd /d "%~dp0"

REM Launcher for the AI Comic Drama workbench server.
REM If Windows or antivirus warns you, choose "More info" -> "Run anyway".

where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 workbench_server.py
    goto done
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    python workbench_server.py
    goto done
)

echo Python not found. Please install Python 3 and add it to PATH.
pause
exit /b 1

:done
pause
