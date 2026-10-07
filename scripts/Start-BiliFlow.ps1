param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [switch]$RefreshExisting,
    # Also open BiliFlow to a phone or laptop on the home Wi-Fi (access code; Start-BiliFlow-Phone.cmd).
    [switch]$Phone,
    [int]$PhonePort = 8767,
    # Where the phone mode opens: wifi (home Wi-Fi) or tailscale (from anywhere; Start-BiliFlow-Tailscale.cmd).
    [ValidateSet('wifi', 'tailscale')]
    [string]$PhoneNetwork = 'wifi'
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

function Get-PhoneStatus([string]$Url) {
    # GET /api/phone-mode (no token needed on 127.0.0.1); $null when the build has no phone mode.
    try { return Invoke-RestMethod -Uri ($Url + 'api/phone-mode') -TimeoutSec 5 } catch { return $null }
}

function Show-PhoneNotice([string]$Url) {
    # Start-BiliFlow.cmd reusing a Control Center whose phone mode is on says so (plan 13.1, H3).
    $Status = Get-PhoneStatus $Url
    if ($Status -and $Status.enabled) {
        Write-Host "NOTE: phone mode is ON: $($Status.url) (turn it off in Dashboard V2 > Cai dat)."
    }
}

function Enable-PhoneMode([string]$Url) {
    # Turns on the phone listener of the running Control Center (no restart) and prints link and code.
    $State = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    $Headers = @{ 'X-BiliFlow-Token' = [string]$State.token }
    $Body = @{ enabled = $true; port = $PhonePort; network = $PhoneNetwork } | ConvertTo-Json -Compress
    try {
        $Result = Invoke-RestMethod -Uri ($Url + 'api/phone-mode') -Method Post -Headers $Headers `
            -ContentType 'application/json' -Body $Body -TimeoutSec 15
    } catch {
        $Status = $null
        if ($_.Exception.Response) { $Status = [int]$_.Exception.Response.StatusCode }
        if ($Status -eq 404) {
            throw ('The running Control Center has no phone mode yet (older build). It was NOT restarted. ' +
                   'When no video is processing, stop it with Stop-BiliFlow.cmd, then run Start-BiliFlow-Phone.cmd again.')
        }
        $Detail = if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { $_.Exception.Message }
        if ($null -eq $Status) {
            # No HTTP answer (timeout, connection reset): the mode may still have been turned on (H5).
            $Now = Get-PhoneStatus $Url
            if ($Now -and $Now.enabled -and $Now.code) {
                Write-Host "The request failed ($Detail), but the phone mode IS on:"
                $Result = $Now
            } elseif ($Now) {
                throw "Phone mode is NOT on (the request failed: $Detail). Try Start-BiliFlow-Phone.cmd again."
            } else {
                throw "Phone mode state is unknown: the Control Center did not answer ($Detail)."
            }
        } else {
            throw "Phone mode could not start: $Detail"
        }
    }
    $Line = '=' * 64
    Write-Host $Line
    # The banner follows the answer: an older Control Center has no network field and opens the home Wi-Fi.
    if ($Result.network -eq 'tailscale') {
        Write-Host '  BILIFLOW TREN DIEN THOAI / LAPTOP (qua Tailscale, o bat ky dau)'
        Write-Host "  Mo tren dien thoai:  $($Result.url)  (bat Tailscale tren dien thoai truoc)"
        Write-Host '  Thiet bi cung tai khoan Tailscale voi PC vao thang, khong can ma.'
        Write-Host "  Ma cho thiet bi khac: $($Result.code)"
        Write-Host '  Tuong lua: can them mot rule cho Tailscale (docs\DASHBOARD_V2_PHONE.md muc 8). KHONG bam Cancel.'
        Write-Host '  PC phai bat va khong ngu. Dung dung link so o tren, khong dung ten may.'
        Write-Host '  Tu tat sau 8 gio, hoac khi Tailscale tren PC tat hoac doi dia chi.'
    } else {
        Write-Host '  BILIFLOW TREN DIEN THOAI / LAPTOP (cung Wi-Fi nha)'
        Write-Host "  Mo tren dien thoai:  $($Result.url)"
        Write-Host "  Nhap ma truy cap:    $($Result.code)"
        Write-Host '  Tuong lua: KHONG bam Cancel (Cancel tao rule Block cho Python, chan ca cong 8767).'
        Write-Host '  Thu tu: dat Wi-Fi la Private -> tao rule Python + cong 8767 -> thu -> don rule cu (docs\DASHBOARD_V2_PHONE.md muc 2);'
        Write-Host '  hoac khi Windows hoi: chon Private networks, khong chon Public, roi bam Allow.'
        Write-Host '  Chi dung trong Wi-Fi nha: ket noi HTTP, khong ma hoa.'
        Write-Host '  Tu tat sau 8 gio, hoac khi dia chi Wi-Fi cua PC doi.'
    }
    if (-not $Result.network -and $PhoneNetwork -eq 'tailscale') {
        Write-Host '  CHU Y: Control Center dang chay la ban cu, chua co Tailscale: vua bat cho Wi-Fi nha.'
        Write-Host '  Khi khong co video dang xu ly: tat bang Stop-BiliFlow.cmd roi chay lai Start-BiliFlow-Tailscale.cmd.'
    } elseif ($Result.network -and $Result.network -ne $PhoneNetwork) {
        Write-Host "  CHU Y: che do dien thoai DA BAT san cho '$($Result.network)'. Muon doi: tat roi chay lai."
    }
    Write-Host '  Tat: nut "Tat che do dien thoai" trong Dashboard V2 > Cai dat tren PC, hoac Stop-BiliFlow.cmd (tat ca hai).'
    Write-Host $Line
}

function Open-Page([string]$Url) {
    if ($NoBrowser) { return }
    # Phone mode opens the V2 settings, where the "Mo tren dien thoai" panel shows link and code.
    if ($Phone) { Start-Process ($Url + 'dashboard-v2/#settings') } else { Start-Process $Url }
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
        if ($Phone) { Enable-PhoneMode $Url } else { Show-PhoneNotice $Url }
        Open-Page $Url
        exit 0
    }

    $ExistingUrl = Get-RunningUrl
    if ($ExistingUrl) {
        Write-Host "BiliFlow is already running: $ExistingUrl"
        # Reused as is: the phone mode is switched on in the running Control Center, never a second one.
        if ($Phone) { Enable-PhoneMode $ExistingUrl } else { Show-PhoneNotice $ExistingUrl }
        Open-Page $ExistingUrl
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
    if ($Phone) { Enable-PhoneMode $Url }
    Open-Page $Url
} finally {
    if ($OwnsLauncherMutex) {
        $LauncherMutex.ReleaseMutex()
    }
    $LauncherMutex.Dispose()
}
