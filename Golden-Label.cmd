@echo off
rem Opens the Golden Set labeling page on http://127.0.0.1:8766 (local only). Close this window to stop.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\golden-label.ps1" %*
set "_golden_exit=%errorlevel%"
if not "%_golden_exit%"=="0" (
  echo.
  echo Trang gan nhan khong mo duoc. Loi o phia tren.
  pause
)
exit /b %_golden_exit%
