@echo off
rem Uninstall: remove the Startup shortcut and stop the running server
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\APEX Agent Office.lnk" 2>nul
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\APEX Agent Office.lnk" (echo [APEX] FAIL: shortcut still there) else (echo [APEX] OK: autostart removed)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*agent_office_server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('[APEX] stopped pid ' + $_.ProcessId) }"
pause
