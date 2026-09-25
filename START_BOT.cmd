@echo off
cd /d "%~dp0"
start "PumpScanBot" powershell.exe -NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -File "%~dp0start_bot.ps1"
exit /b
