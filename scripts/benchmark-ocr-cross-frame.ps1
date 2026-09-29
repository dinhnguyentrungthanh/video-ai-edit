# Dedicated runner: adding benchmark commands must not invalidate production
# stage caches by changing their fingerprinted scripts/run.ps1 entry point.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$BenchmarkArgs)
. (Join-Path $PSScriptRoot 'env.ps1')
$BenchmarkRoot = Split-Path -Parent $PSScriptRoot
$BenchmarkPython = Join-Path $BenchmarkRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $BenchmarkPython)) {
    throw "BiliFlow Python environment is missing: $BenchmarkPython"
}
$BenchmarkMutex = [Threading.Mutex]::new($false, 'Local\BiliFlowGpuInference')
$BenchmarkHasMutex = $false
$BenchmarkExitCode = 1
try {
    Write-Host 'BiliFlow: waiting for the GPU benchmark slot'
    try {
        $BenchmarkHasMutex = $BenchmarkMutex.WaitOne()
    } catch [Threading.AbandonedMutexException] {
        $BenchmarkHasMutex = $true
    }
    Write-Host 'BiliFlow: GPU benchmark slot acquired'
    & $BenchmarkPython (Join-Path $PSScriptRoot 'benchmark_ocr_cross_frame.py') @BenchmarkArgs
    $BenchmarkExitCode = $LASTEXITCODE
} finally {
    if ($BenchmarkHasMutex) { $BenchmarkMutex.ReleaseMutex() }
    $BenchmarkMutex.Dispose()
}
exit $BenchmarkExitCode
