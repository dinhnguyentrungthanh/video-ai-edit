# Stops every running Golden Set labeling page (PC or phone mode). Labels are saved on every
# click, so nothing is lost; the phone link stops working because its code dies with the server.
$servers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*golden_label_server.py*' })
foreach ($server in $servers) {
    Stop-Process -Id $server.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue
}
Get-ChildItem -Path (Join-Path $PSScriptRoot '..\reports\benchmarks') -Directory -Filter 'golden-*' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $_.FullName 'phone-link.txt') }
if ($servers.Count -gt 0) {
    Write-Host 'Da tat trang gan nhan. Nhan da duoc luu san.'
} else {
    Write-Host 'Trang gan nhan dang khong chay.'
}
