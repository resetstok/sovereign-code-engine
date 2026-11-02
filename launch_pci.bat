@echo off
set "ROOT_DIR=C:\Lab\013 - Sovereign-Code-Engine\"
set "PYTHONPATH=C:\Lab\013 - Sovereign-Code-Engine;C:\Lab\013 - Sovereign-Code-Engine\src"
set "PYTHONW_BIN=C:\Lab\012 - Auditoria (SESAU)\.venv\Scripts\pythonw.exe"
cd /d "C:\Lab\013 - Sovereign-Code-Engine"
start "" "%PYTHONW_BIN%" "src\pci_engine\gui_app.py"
exit /b 0
