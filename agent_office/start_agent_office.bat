@echo off
rem APEX Agent Office launcher (pure ASCII on purpose: cp950 console)
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo [APEX] Agent Office: http://127.0.0.1:8777/
echo [APEX] Keep this window open. Close it to stop. Log: %~dp0agent_office.log
python -X utf8 "%~dp0agent_office_server.py"
if errorlevel 1 pause
