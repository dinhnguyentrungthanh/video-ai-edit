@echo off
rem BiliFlow on a phone or laptop from anywhere, over Tailscale (access code required).
rem Same as Start-BiliFlow-Phone.cmd, but the phone mode listens on the PC's Tailscale address.
rem Tailscale must be installed and signed in on the PC and the phone (docs\DASHBOARD_V2_PHONE.md section 8).
title BiliFlow - mo qua Tailscale
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-BiliFlow.ps1" -Phone -PhoneNetwork tailscale %*
set "_biliflow_exit=%errorlevel%"
if not "%_biliflow_exit%"=="0" (
  echo.
  echo Che do dien thoai khong mo duoc. Loi o phia tren.
)
echo.
echo Giu cua so nay de xem lai link va ma, roi bam phim bat ky de dong.
pause
exit /b %_biliflow_exit%
