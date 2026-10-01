@echo off
rem Golden Set labeling page reachable from a phone on the same home Wi-Fi (access code required).
rem Keep this window open while labeling; close it or run Golden-Label-Stop.cmd to stop.
title BiliFlow - gan nhan qua dien thoai
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\golden-label.ps1" --phone %*
set "_golden_exit=%errorlevel%"
if not "%_golden_exit%"=="0" (
  echo.
  echo Trang gan nhan khong mo duoc. Loi o phia tren.
  pause
)
exit /b %_golden_exit%
