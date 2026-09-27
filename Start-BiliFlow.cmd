@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-BiliFlow.ps1" %*
set "_biliflow_exit=%errorlevel%"
if not "%_biliflow_exit%"=="0" (
  echo.
  echo BiliFlow could not start. The error is shown above.
  pause
)
exit /b %_biliflow_exit%
