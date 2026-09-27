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
    'scan-animation-safety', 'scan-visual-logo',
    'confirm-violence', 'benchmark-images', 'benchmark-videos', 'benchmark-ad-pipeline',
    'localize-visual-logo', 'augment-grounding-regions'
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
        $HasMutex = $ResourceMutex.WaitOne()
        Write-Host "BiliFlow: resource slot acquired"
    }
    if ($CommandName -eq 'localize-visual-logo') {
        $LocalizerArgs = if ($BiliflowArgs.Count -gt 1) {
            $BiliflowArgs[1..($BiliflowArgs.Count - 1)]
        } else {
            @()
        }
        & $PythonExe (Join-Path $PSScriptRoot 'localize_visual_logo_report.py') @LocalizerArgs
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

