@echo off
rem Golden Set v1.1 labeling page (this PC). Close the window or run Golden-Label-Stop.cmd to stop.
title BiliFlow - gan nhan v1.1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\golden-label.ps1" --set v1.1 %*
set "_golden_exit=%errorlevel%"
if not "%_golden_exit%"=="0" (
  echo.
  echo Trang gan nhan khong mo duoc. Loi o phia tren.
  pause
)
exit /b %_golden_exit%
