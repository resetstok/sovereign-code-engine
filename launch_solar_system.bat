@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=C:\Lab\012 - Auditoria (SESAU)\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
start "" "%PYTHON_EXE%" src/visualization.py
exit /b 0
