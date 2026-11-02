@echo off
rem ===========================================================================
rem PCI Master System v2.0 - Launcher Silencioso para Área de Trabalho
rem ===========================================================================

set "ROOT_DIR=%~dp0"
set "PYTHONPATH=%ROOT_DIR%;%ROOT_DIR%src;%PYTHONPATH%"

set "PYTHONW_BIN="
if exist "%ROOT_DIR%.venv\Scripts\pythonw.exe" (
    set "PYTHONW_BIN=%ROOT_DIR%.venv\Scripts\pythonw.exe"
)
if not defined PYTHONW_BIN (
    if exist "C:\Lab\012 - Auditoria (SESAU)\.venv\Scripts\pythonw.exe" (
        set "PYTHONW_BIN=C:\Lab\012 - Auditoria (SESAU)\.venv\Scripts\pythonw.exe"
    )
)
if not defined PYTHONW_BIN (
    set "PYTHONW_BIN=pythonw.exe"
)

cd /d "%ROOT_DIR%"
start "" "%PYTHONW_BIN%" "%ROOT_DIR%src\pci_engine\gui_app.py" %*
exit /b 0
