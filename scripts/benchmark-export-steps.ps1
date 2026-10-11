# Export step timings and the blur gate (scripts/benchmark_export_steps.py) under the shared GPU slot and the final-render slot.
# -InstallRoot defaults to this checkout; a worktree passes the install root (its .venv, tools and temp).
param(
    [string]$InstallRoot = (Split-Path -Parent $PSScriptRoot),
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$BenchArgs
)
$PythonExe = Join-Path $InstallRoot '.venv\Scripts\python.exe'
$env:TEMP = Join-Path $InstallRoot 'temp'
$env:TMP = $env:TEMP
$GpuMutex = [Threading.Mutex]::new($false, 'Local\BiliFlowGpuInference')
$RenderMutex = [Threading.Mutex]::new($false, 'Local\BiliFlowFinalRender')
$HasGpu = $false
$HasRender = $false
$Code = 1
try {
    Write-Host 'BiliFlow benchmark: waiting for Local\BiliFlowGpuInference'
    try { $HasGpu = $GpuMutex.WaitOne() } catch [Threading.AbandonedMutexException] { $HasGpu = $true }
    Write-Host 'BiliFlow benchmark: waiting for Local\BiliFlowFinalRender'
    try { $HasRender = $RenderMutex.WaitOne() } catch [Threading.AbandonedMutexException] { $HasRender = $true }
    Write-Host 'BiliFlow benchmark: slots held'
    & $PythonExe (Join-Path $PSScriptRoot 'benchmark_export_steps.py') --install-root $InstallRoot @BenchArgs
    $Code = $LASTEXITCODE
} finally {
    if ($HasRender) { $RenderMutex.ReleaseMutex() }
    if ($HasGpu) { $GpuMutex.ReleaseMutex() }
    $RenderMutex.Dispose()
    $GpuMutex.Dispose()
}
exit $Code
