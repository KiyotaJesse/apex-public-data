' APEX Agent Office hidden launcher (pure ASCII on purpose - cp950 lesson)
' Runs agent_office_server.py with no console window and no browser popup.
' stdout/stderr go to agent_office_boot.log next to this file (python missing etc. shows up there).
Set fso = CreateObject("Scripting.FileSystemObject")
d = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "cmd /c cd /d """ & d & """ && set ""PYTHONIOENCODING=utf-8"" && set ""AGENT_OFFICE_NO_BROWSER=1"" && python -X utf8 agent_office_server.py >> agent_office_boot.log 2>&1"
CreateObject("Wscript.Shell").Run cmd, 0, False
