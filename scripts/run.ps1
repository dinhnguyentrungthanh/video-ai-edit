param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$BiliflowArgs
)

. (Join-Path $PSScriptRoot 'env.ps1')
$BiliflowRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $BiliflowRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Biliflow Python environment is missing: $PythonExe"
}

$CommandName = if ($BiliflowArgs.Count -gt 0) { $BiliflowArgs[0] } else { '' }
$UsesCuda = -not ($BiliflowArgs -contains '--device' -and $BiliflowArgs[([Array]::IndexOf($BiliflowArgs, '--device') + 1)] -eq 'cpu')
$GpuCommands = @(
    'scan', 'scan-text', 'classify-text', 'scan-content',
    'scan-animation-safety', 'scan-live-safety', 'scan-visual-logo',
    'confirm-violence', 'benchmark-images', 'benchmark-videos', 'benchmark-ad-pipeline',
    'localize-visual-logo', 'augment-grounding-regions', 'benchmark-scan-timing', 'benchmark-frame-prefetch', 'benchmark-ocr-batch'
)
$MutexName = $null
if ($UsesCuda -and $GpuCommands -contains $CommandName) {
    $MutexName = 'Local\BiliFlowGpuInference'
} elseif ($CommandName -eq 'render-final') {
    $MutexName = 'Local\BiliFlowFinalRender'
}

$ResourceMutex = $null
$HasMutex = $false
try {
    if ($MutexName) {
        $ResourceMutex = [Threading.Mutex]::new($false, $MutexName)
        Write-Host "BiliFlow: waiting for resource slot $MutexName"
        $SlotWait = [Diagnostics.Stopwatch]::StartNew()
        $HasMutex = $ResourceMutex.WaitOne()
        $SlotWait.Stop()
        Write-Host "BiliFlow: resource slot acquired"
        Write-Host ("BiliFlow performance: resource_wait_seconds={0:F3}" -f $SlotWait.Elapsed.TotalSeconds)
    }
    if ($CommandName -eq 'localize-visual-logo') {
        $LocalizerArgs = if ($BiliflowArgs.Count -gt 1) {
            $BiliflowArgs[1..($BiliflowArgs.Count - 1)]
        } else {
            @()
        }
        & $PythonExe (Join-Path $PSScriptRoot 'localize_visual_logo_report.py') @LocalizerArgs
    } elseif ($CommandName -eq 'benchmark-scan-timing') {
        $TimingArgs = if ($BiliflowArgs.Count -gt 1) { $BiliflowArgs[1..($BiliflowArgs.Count - 1)] } else { @() }
        & $PythonExe (Join-Path $PSScriptRoot 'benchmark_scan_timing.py') @TimingArgs
    } elseif ($CommandName -eq 'benchmark-frame-prefetch') {
        $PrefetchArgs = if ($BiliflowArgs.Count -gt 1) { $BiliflowArgs[1..($BiliflowArgs.Count - 1)] } else { @() }
        & $PythonExe (Join-Path $PSScriptRoot 'benchmark_frame_prefetch.py') @PrefetchArgs
    } elseif ($CommandName -eq 'benchmark-ocr-batch') {
        $BatchArgs = if ($BiliflowArgs.Count -gt 1) { $BiliflowArgs[1..($BiliflowArgs.Count - 1)] } else { @() }
        & $PythonExe (Join-Path $PSScriptRoot 'benchmark_ocr_batch.py') @BatchArgs
    } else {
        & $PythonExe -m biliflow @BiliflowArgs
    }
    $ExitCode = $LASTEXITCODE
} finally {
    if ($HasMutex -and $ResourceMutex) {
        $ResourceMutex.ReleaseMutex()
    }
    if ($ResourceMutex) {
        $ResourceMutex.Dispose()
    }
}
exit $ExitCode

