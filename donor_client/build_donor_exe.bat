@echo off
setlocal EnableDelayedExpansion
REM =========================================================================
REM PCI Sovereign Code Engine - Build Script do Agente Doador Executavel
REM Empacota worker_agent.py em um unico arquivo standalone PCI_Donor_Agent.exe
REM =========================================================================

echo [PCI ENGINE] Iniciando compilacao do PCI Donor Worker Agent...

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "PYTHON_EXE="
if exist "C:\Lab\012 - Auditoria (SESAU)\.venv\Scripts\python.exe" set "PYTHON_EXE=C:\Lab\012 - Auditoria (SESAU)\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "..\..\012 - Auditoria (SESAU)\.venv\Scripts\python.exe" set "PYTHON_EXE=..\..\012 - Auditoria (SESAU)\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if defined VIRTUAL_ENV set "PYTHON_EXE=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

echo [PCI ENGINE] Usando Python: "!PYTHON_EXE!"

REM Verifica se PyInstaller esta instalado
"!PYTHON_EXE!" -m PyInstaller --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERRO] PyInstaller nao encontrado. Instalando dependencias de build...
    "!PYTHON_EXE!" -m pip install pyinstaller customtkinter psutil
)

echo [PCI ENGINE] Gerando executavel standalone (PCI_Donor_Agent.exe)...

"!PYTHON_EXE!" -m PyInstaller ^
    --onefile ^
    --noconsole ^
    --name PCI_Donor_Agent ^
    --collect-all customtkinter ^
    --hidden-import psutil ^
    --hidden-import tkinter ^
    --hidden-import urllib.request ^
    --clean ^
    worker_agent.py

if %ERRORLEVEL% equ 0 (
    echo =========================================================================
    echo [SUCESSO] PCI_Donor_Agent.exe gerado com sucesso!
    echo Localizacao: %SCRIPT_DIR%dist\PCI_Donor_Agent.exe
    echo =========================================================================
) else (
    echo [ERRO] Falha na compilacao do agente doador executavel.
    exit /b 1
)
