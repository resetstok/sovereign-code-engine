@echo off
echo ===================================================
echo  Compilando Executavel Seguro do Agente Doador v2.0
echo ===================================================

pip install pyinstaller requests psutil

pyinstaller --onefile --name "pci_donor_worker" ^
    --add-data "pci_worker_workspace;pci_worker_workspace" ^
    worker_agent.py

echo.
echo Executavel gerado em: dist\pci_donor_worker.exe
pause
