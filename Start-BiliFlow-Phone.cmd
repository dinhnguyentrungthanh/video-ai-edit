@echo off
rem BiliFlow on a phone or laptop on the same home Wi-Fi (access code required).
rem Same launcher as Start-BiliFlow.cmd plus -Phone: starts or reuses the Control Center,
rem turns the phone mode on without restarting it, then prints the link and the code.
title BiliFlow - mo tren dien thoai
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-BiliFlow.ps1" -Phone %*
set "_biliflow_exit=%errorlevel%"
if not "%_biliflow_exit%"=="0" (
  echo.
  echo Che do dien thoai khong mo duoc. Loi o phia tren.
)
echo.
echo Giu cua so nay de xem lai link va ma, roi bam phim bat ky de dong.
pause
exit /b %_biliflow_exit%
