param([Parameter(ValueFromRemainingArguments = $true)][string[]]$StageArgs)
$StageRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $StageRoot 'scripts\env.ps1')
if ($StageArgs[0] -notin @('scan-visual-logo', 'augment-grounding-regions')) {
    throw 'This isolated benchmark runner accepts only logo and grounding stages'
}
$StageMutex = [Threading.Mutex]::new($false, 'Local\BiliFlowGpuInference')
$StageLocked = $false
$StageCode = 1
try {
    $StageWait = [Diagnostics.Stopwatch]::StartNew()
    try { $StageLocked = $StageMutex.WaitOne() } catch [Threading.AbandonedMutexException] { $StageLocked = $true }
    $StageWait.Stop()
    Write-Host ("BiliFlow benchmark: resource_wait_seconds={0:F3}" -f $StageWait.Elapsed.TotalSeconds)
    & (Join-Path $StageRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'benchmark_cold_ad_stage.py') @StageArgs
    $StageCode = $LASTEXITCODE
} finally {
    if ($StageLocked) { $StageMutex.ReleaseMutex() }
    $StageMutex.Dispose()
}
exit $StageCode
