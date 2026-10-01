# Opens the Golden Set labeling page on http://127.0.0.1:8766; --phone also opens it to a phone on the home Wi-Fi (access code).
. (Join-Path $PSScriptRoot 'env.ps1')
& (Join-Path $BiliflowRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'golden_label_server.py') @args
exit $LASTEXITCODE
