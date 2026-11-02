@echo off
rem ===========================================================================
rem PCI Master System v2.0 - Launcher Silencioso para Área de Trabalho
rem Executa o gui_app.py via pythonw.exe sem manter janela de prompt preta aberta
rem ===========================================================================

set ROOT_DIR=%~dp0
set PYTHONPATH=%ROOT_DIR%;%ROOT_DIR%src;%PYTHONPATH%

rem 1. Procura pythonw no venv local ou no venv do Lab
if exist "%ROOT_DIR%.venv\Scripts\pythonw.exe" (
    set PYTHONW_BIN=%ROOT_DIR%.venv\Scripts\pythonw.exe
) else if exist "C:\Lab\012 - Auditoria (SESAU)\.venv\Scripts\pythonw.exe" (
    set PYTHONW_BIN=C:\Lab\012 - Auditoria (SESAU)\.venv\Scripts\pythonw.exe
) else (
    set PYTHONW_BIN=pythonw.exe
)

cd /d "%ROOT_DIR%"
start "" "%PYTHONW_BIN%" "%ROOT_DIR%src\pci_engine\gui_app.py" %*
exit /b 0
