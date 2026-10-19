@echo off
rem Install: start APEX Agent Office hidden at every Windows logon (Startup folder shortcut, no admin needed)
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0agent_office_hidden.vbs" (echo [APEX] FAIL: agent_office_hidden.vbs not found in %~dp0 & pause & exit /b 1)
if not exist "%~dp0agent_office_server.py" (echo [APEX] FAIL: agent_office_server.py not found in %~dp0 & pause & exit /b 1)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=[Environment]::GetFolderPath('Startup')+'\APEX Agent Office.lnk'; $s=(New-Object -ComObject WScript.Shell).CreateShortcut($p); $s.TargetPath='wscript.exe'; $s.Arguments='\"%~dp0agent_office_hidden.vbs\"'; $s.WorkingDirectory='%~dp0'; $s.Description='APEX Agent Office http://127.0.0.1:8777/'; $s.Save(); if(Test-Path $p){Write-Host ('[APEX] OK: shortcut created '+$p)}else{Write-Host '[APEX] FAIL: shortcut not created'; exit 1}"
if errorlevel 1 (pause & exit /b 1)
echo [APEX] Starting it now in the background (no window)...
wscript.exe "%~dp0agent_office_hidden.vbs"
powershell -NoProfile -Command "Start-Sleep 6; try{$r=Invoke-WebRequest http://127.0.0.1:8777/api/health -UseBasicParsing -TimeoutSec 5; Write-Host ('[APEX] OK: server answers '+$r.Content)}catch{Write-Host '[APEX] FAIL: server did not answer. See agent_office_boot.log and agent_office.log in this folder.'; exit 1}"
echo [APEX] Open http://127.0.0.1:8777/ in your browser. It will now start by itself after every logon.
pause
