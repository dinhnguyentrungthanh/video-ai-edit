@echo off
rem Stops every running Golden Set labeling page (PC or phone mode). Labels are already saved.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\golden-label-stop.ps1"
timeout /t 3 >nul
