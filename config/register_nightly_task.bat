@echo off
setlocal EnableDelayedExpansion
REM =========================================================================
REM PCI Sovereign Code Engine - Agendador Noturno Colibri (Windows Task Scheduler)
REM Registra execucao diaria as 03:00 AM para processamento em lote (--mmap 1).
REM =========================================================================

echo [PCI ENGINE] Configurando Agendamento Noturno no Windows...

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "REPO_DIR=%CD%"

set "PYTHON_EXE="
if exist "C:\Lab\012 - Auditoria (SESAU)\.venv\Scripts\python.exe" set "PYTHON_EXE=C:\Lab\012 - Auditoria (SESAU)\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "..\012 - Auditoria (SESAU)\.venv\Scripts\python.exe" set "PYTHON_EXE=%REPO_DIR%\..\012 - Auditoria (SESAU)\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if defined VIRTUAL_ENV set "PYTHON_EXE=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python.exe"

echo [PCI ENGINE] Usando Python: "!PYTHON_EXE!"
echo [PCI ENGINE] Diretorio Raiz: "!REPO_DIR!"

REM Monta o comando completo a ser executado pelo Windows Task Scheduler
set "TASK_CMD=\"!PYTHON_EXE!\" -m pci_engine.nightly_colibri"

echo [PCI ENGINE] Registrando tarefa no Windows Task Scheduler para as 03:00 AM...

schtasks /create ^
    /tn "PCI_Sovereign_Nightly_Colibri" ^
    /tr "!TASK_CMD!" ^
    /sc daily ^
    /st 03:00 ^
    /ru "%USERNAME%" ^
    /f

if %ERRORLEVEL% equ 0 (
    echo =========================================================================
    echo [SUCESSO] Tarefa 'PCI_Sovereign_Nightly_Colibri' agendada com exito!
    echo Horario: Diariamente as 03:00 AM
    echo Para verificar: schtasks /query /tn "PCI_Sovereign_Nightly_Colibri"
    echo Para executar agora: schtasks /run /tn "PCI_Sovereign_Nightly_Colibri"
    echo =========================================================================
) else (
    echo [AVISO] Nao foi possivel registrar via schtasks padrao.
    echo Certifique-se de executar este script em um terminal como Administrador.
)
