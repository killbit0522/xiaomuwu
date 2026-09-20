@echo off
cd /d "%~dp0"
start "xiaomuwu-server" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-local-site.ps1" -Port 4175
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:4175/pages/home.html"
