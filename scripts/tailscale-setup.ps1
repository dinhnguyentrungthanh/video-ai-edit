# BiliFlow: the only code that runs as administrator for Tailscale (docs/TAILSCALE_PLAN.md).
# Started by src/biliflow/tailscale_manager.py after the Windows UAC prompt, when the user presses
# "Cai va cau hinh Tailscale", "Khoi dong dich vu Tailscale" or "Tao rule tuong lua" on the PC.
# Everything it acts on is on its command line, fixed when Windows started it: the action (one of
# three), and for an install the MSI file name and the SHA-256 Tailscale published for it. The MSI
# must be <root>\cache\tailscale\<name>; it is held open (others may only read it) from the hash and
# signature checks until msiexec ends, so it cannot be swapped in between. Every other path is
# computed from the script's own folder; junctions and symlinks are refused. The result JSON goes to
# <root>\temp\tailscale\result-<ResultId>.json and is never written over an existing file.
# Tailscale itself goes to its default folder, %ProgramFiles%\Tailscale (the user's choice on 2026-10-07:
# its SYSTEM service must not run from the BiliFlow tree, which every local account can modify).
param(
    [Parameter(Mandatory = $true)][ValidateSet('install', 'start', 'firewall')][string]$Action,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{16}$')][string]$ResultId,
    [string]$MsiName = '',
    [string]$Sha256 = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$WorkDir = Join-Path $Root 'temp\tailscale'
$CacheDir = Join-Path $Root 'cache\tailscale'
$LogDir = Join-Path $Root 'logs\tailscale'
$RuleName = 'BiliFlow phone mode Tailscale (Python, TCP 8767)'
$Steps = New-Object System.Collections.ArrayList

function Test-NoLink([string]$Path) {
    # A junction or symlink could send an administrator write or read somewhere else.
    $Item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw "Refusing a link: $Path" }
}

function Add-Step([string]$Name, [bool]$Ok, [string]$Detail) {
    [void]$Steps.Add([ordered]@{ name = $Name; ok = $Ok; detail = $Detail })
}

function Write-Result([string]$ResultPath, [bool]$Ok, [string]$ErrorText) {
    $Json = [ordered]@{ ok = $Ok; action = $Action; steps = @($Steps); error = $ErrorText } | ConvertTo-Json -Depth 5
    $Bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Json)
    $Stream = [System.IO.File]::Open($ResultPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None)
    try { $Stream.Write($Bytes, 0, $Bytes.Length) } finally { $Stream.Dispose() }
}

function Get-BiliflowPython {
    # Same choice as docs\DASHBOARD_V2_PHONE.md section 2: the newest cpython-3.11.<patch> folder.
    $Dir = Get-ChildItem (Join-Path $Root 'runtime\python') -Directory -Filter 'cpython-3.11.*-windows-x86_64-none' |
        Sort-Object { [version]($_.Name -replace '^cpython-(\d+\.\d+\.\d+)-.*$', '$1') } -Descending |
        Select-Object -First 1
    if (-not $Dir) { throw 'BiliFlow Python not found in runtime\python' }
    $Python = Join-Path $Dir.FullName 'python.exe'
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "BiliFlow Python not found: $Python" }
    return $Python
}

function Set-FirewallRule {
    $Python = Get-BiliflowPython
    Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Program $Python `
        -Protocol TCP -LocalPort 8767 -Profile Private -RemoteAddress 100.64.0.0/10 | Out-Null
    Add-Step 'firewall' $true $Python
}

function Test-Installer([string]$Msi, [string]$Expected) {
    $Hash = (Get-FileHash -LiteralPath $Msi -Algorithm SHA256).Hash
    if ($Hash -ne $Expected.ToUpperInvariant()) { throw 'The installer does not match the published SHA-256' }
    $Signature = Get-AuthenticodeSignature -LiteralPath $Msi
    $Subject = [string]$Signature.SignerCertificate.Subject
    if ([string]$Signature.Status -ne 'Valid' -or $Subject -notmatch '(^|,\s*)(O|CN)=Tailscale Inc\.(,|$)') {
        throw "The installer is not validly signed by Tailscale Inc. ($($Signature.Status); $Subject)"
    }
    Add-Step 'verify' $true $Hash
}

function Start-TailscaleService {
    $Service = Get-Service -Name Tailscale -ErrorAction Stop
    if ($Service.Status -ne 'Running') {
        Start-Service -Name Tailscale
        $Service.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    }
    Add-Step 'service' $true 'Running'
}

function Install-Tailscale {
    if ($MsiName -cnotmatch '^tailscale-setup-[0-9]+(\.[0-9]+){1,3}-amd64\.msi$') { throw 'Not a Tailscale MSI name' }
    if ($Sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'No expected SHA-256' }
    Test-NoLink (Join-Path $Root 'cache')
    Test-NoLink $CacheDir
    $Msi = Join-Path $CacheDir $MsiName
    if (-not (Test-Path -LiteralPath $Msi -PathType Leaf)) { throw 'The installer file is missing' }
    Test-NoLink $Msi
    $Lock = [System.IO.File]::Open($Msi, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read)
    try {
        Test-Installer $Msi $Sha256
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
        $Log = Join-Path $LogDir ('msiexec-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
        $Arguments = @('/i', ('"' + $Msi + '"'), '/quiet', '/norestart',
            'TS_UNATTENDEDMODE=always', 'TS_INSTALLUPDATES=always', 'TS_NOLAUNCH=1', '/L*v', ('"' + $Log + '"'))
        $Process = Start-Process -FilePath ([System.Environment]::SystemDirectory + '\msiexec.exe') `
            -ArgumentList $Arguments -Wait -PassThru -WindowStyle Hidden
    } finally {
        $Lock.Dispose()
    }
    if ($Process.ExitCode -ne 0 -and $Process.ExitCode -ne 3010) {
        Add-Step 'msiexec' $false ([string]$Process.ExitCode)
        throw "msiexec failed with exit code $($Process.ExitCode) (log: $Log)"
    }
    Add-Step 'msiexec' $true ([string]$Process.ExitCode)
    Start-TailscaleService
    Set-FirewallRule
}

try {
    Test-NoLink (Join-Path $Root 'temp')
    Test-NoLink $WorkDir
} catch {
    exit 2
}
$ResultPath = Join-Path $WorkDir ('result-' + $ResultId + '.json')
try {
    switch ($Action) {
        'install' { Install-Tailscale }
        'start' { Start-TailscaleService }
        'firewall' { Set-FirewallRule }
    }
    Write-Result $ResultPath $true $null
    exit 0
} catch {
    try { Write-Result $ResultPath $false $_.Exception.Message } catch { exit 3 }
    exit 1
}
