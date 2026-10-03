@echo off
setlocal
cd /d "%~dp0.."
set "TEMP=%CD%\temp"
set "TMP=%TEMP%"
set "PYTHONDONTWRITEBYTECODE=1"
".venv\Scripts\python.exe" -B "dashboard_v2\serve.py" --port 8794
endlocal
