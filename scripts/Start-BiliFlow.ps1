param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [switch]$RefreshExisting
)

$BiliflowRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $BiliflowRoot '.venv\Scripts\python.exe'
$StatePath = Join-Path $BiliflowRoot 'state\control-center.json'
$DatabasePath = Join-Path $BiliflowRoot 'state\control-center.sqlite3'
$LogRoot = Join-Path $BiliflowRoot 'logs\control-center'

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Biliflow Python environment is missing: $PythonExe"
}
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

function Get-RunningUrl {
    if (-not (Test-Path -LiteralPath $StatePath)) { return $null }
    try {
        $State = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
        Invoke-RestMethod -Uri ($State.url + 'healthz') -TimeoutSec 2 | Out-Null
        return [string]$State.url
    } catch { return $null }
}

$LauncherMutex = [Threading.Mutex]::new($false, 'Local\BiliFlowControlCenterLauncher')
$OwnsLauncherMutex = $false
try {
    try {
        $OwnsLauncherMutex = $LauncherMutex.WaitOne(0)
    } catch [Threading.AbandonedMutexException] {
        $OwnsLauncherMutex = $true
    }

    if (-not $OwnsLauncherMutex) {
        Write-Host 'BiliFlow is already starting. Waiting for the existing launch...'
        $WaitDeadline = (Get-Date).AddSeconds(45)
        do {
            Start-Sleep -Milliseconds 350
            $Url = Get-RunningUrl
            if ($Url) { break }
        } while ((Get-Date) -lt $WaitDeadline)
        if (-not $Url) {
            throw 'The existing BiliFlow launch did not become ready within 45 seconds.'
        }
        if (-not $NoBrowser) { Start-Process $Url }
        exit 0
    }

    $ExistingUrl = Get-RunningUrl
    if ($ExistingUrl) {
        Write-Host "BiliFlow is already running: $ExistingUrl"
        if (-not $NoBrowser) { Start-Process $ExistingUrl }
        exit 0
    }

    $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $OutLog = Join-Path $LogRoot "control-center-$Stamp.out.log"
    $ErrLog = Join-Path $LogRoot "control-center-$Stamp.err.log"
    $Arguments = @(
        '-m', 'biliflow.control_entry', '--project-root', $BiliflowRoot,
        '--host', '127.0.0.1', '--port', [string]$Port
    )
    $HasExistingDatabase = (
        (Test-Path -LiteralPath $DatabasePath) -and
        ((Get-Item -LiteralPath $DatabasePath).Length -gt 0)
    )
    if ($HasExistingDatabase -and -not $RefreshExisting) {
        $Arguments += '--no-import-existing'
        $StartupDeadlineSeconds = 30
        Write-Host 'Starting BiliFlow from the existing database...'
    } else {
        $StartupDeadlineSeconds = 300
        Write-Host 'Starting BiliFlow and importing existing project history. This first sync can take several minutes...'
    }
    Start-Process -FilePath $PythonExe -ArgumentList $Arguments -WorkingDirectory $BiliflowRoot `
        -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog `
        -ErrorAction Stop | Out-Null

    $Deadline = (Get-Date).AddSeconds($StartupDeadlineSeconds)
    do {
        Start-Sleep -Milliseconds 350
        $Url = Get-RunningUrl
        if ($Url) { break }
    } while ((Get-Date) -lt $Deadline)

    if (-not $Url) {
        throw "BiliFlow did not start. Check $ErrLog"
    }
    Write-Host "BiliFlow Control Center: $Url"
    if (-not $NoBrowser) { Start-Process $Url }
} finally {
    if ($OwnsLauncherMutex) {
        $LauncherMutex.ReleaseMutex()
    }
    $LauncherMutex.Dispose()
}
