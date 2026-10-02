# Exact-output speedup equivalence (scripts/benchmark_exact_speedups.py) under the shared GPU inference slot.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$BenchArgs)
. (Join-Path $PSScriptRoot 'env.ps1')
$Mutex = [Threading.Mutex]::new($false, 'Local\BiliFlowGpuInference')
$Locked = $false
$Code = 1
try {
    try { $Locked = $Mutex.WaitOne() } catch [Threading.AbandonedMutexException] { $Locked = $true }
    & (Join-Path $BiliflowRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'benchmark_exact_speedups.py') @BenchArgs
    $Code = $LASTEXITCODE
} finally {
    if ($Locked) { $Mutex.ReleaseMutex() }
    $Mutex.Dispose()
}
exit $Code
