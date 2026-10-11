# BiliFlow "Do lai": the locked hardware measurement (docs/HARDWARE_PROFILE.md section 6.3).
# The Control Center (hardware_manager.py) and a person running it by hand use the same command:
#   powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <code>\scripts\hardware-measure.ps1 `
#     -ProjectRoot <root> -GuardRoot <root> [-GuardRoot <install>] -Python <folder>\.venv\Scripts\python.exe `
#     [-FfmpegDir <folder>] [-Stamp yyyyMMdd-HHmmss-xxxx] [-WaitMinutes N] [-LaunchedBy control-center|script]
# This script only checks its arguments, dot-sources env.ps1 and runs
#   python -m biliflow.hardware_profile measure ...
# returning Python's exit code. Every lock (the mutexes and state\final-render.lock) is taken inside
# Python; this script holds none. Exit codes: 0 ok, 10 failed attempt, 64 bad arguments or root,
# 70 internal error, 75 busy / not idle / cancelled, 124 time budget spent. A PowerShell binding error
# exits 1. Python checks every value again (and refuses a root outside this checkout's install or its
# temp folder); the checks here exist because Windows PowerShell 5.1 drops empty arguments and breaks
# an argument that ends with a backslash or holds a quote. ASCII only (PowerShell 5.1 reads ANSI).
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$ProjectRoot = '',
    [string]$Python = '',
    [string]$FfmpegDir = '',
    [string]$Stamp = '',
    [string]$WaitMinutes = '',
    [string]$LaunchedBy = '',
    # -GuardRoot may repeat. Under -File a repeated typed parameter cannot bind, so the pairs land
    # here and are checked one by one.
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest = @()
)

$ErrorActionPreference = 'Stop'
$UsageExit = 64

function Exit-Usage([string]$Message) {
    [Console]::Error.WriteLine("hardware-measure.ps1: $Message")
    exit $UsageExit
}

function Test-PlainPath([string]$Path) {
    # A drive-absolute path without a quote and without a trailing backslash.
    return ($Path -match '^[A-Za-z]:\\[^"]*$') -and -not $Path.EndsWith('\')
}

function Test-Folder([string]$Path) {
    try {
        return (Test-PlainPath $Path) -and (Test-Path -LiteralPath $Path -PathType Container)
    } catch {
        return $false
    }
}

function Test-VenvPython([string]$Path) {
    # Some <folder>\.venv\Scripts\python.exe that exists (a worktree uses the install's venv).
    try {
        if (-not (Test-PlainPath $Path)) { return $false }
        $ScriptsDir = [IO.Path]::GetDirectoryName($Path)
        if (-not $ScriptsDir) { return $false }
        $VenvDir = [IO.Path]::GetDirectoryName($ScriptsDir)
        if (-not $VenvDir) { return $false }
        return ([IO.Path]::GetFileName($Path) -ieq 'python.exe') -and
            ([IO.Path]::GetFileName($ScriptsDir) -ieq 'Scripts') -and
            ([IO.Path]::GetFileName($VenvDir) -ieq '.venv') -and
            (Test-Path -LiteralPath $Path -PathType Leaf)
    } catch {
        return $false
    }
}

$GuardRoots = New-Object System.Collections.ArrayList
if (($Rest.Count % 2) -ne 0) { Exit-Usage 'unexpected argument' }
for ($Index = 0; $Index -lt $Rest.Count; $Index += 2) {
    if ($Rest[$Index] -ine '-GuardRoot') { Exit-Usage 'unexpected argument' }
    [void]$GuardRoots.Add([string]$Rest[$Index + 1])
}

if (-not (Test-PlainPath $ProjectRoot)) { Exit-Usage '-ProjectRoot must be an absolute folder path' }
foreach ($Guard in $GuardRoots) {
    if (-not (Test-Folder $Guard)) { Exit-Usage '-GuardRoot must be an existing absolute folder' }
}
if ($FfmpegDir -and -not (Test-PlainPath $FfmpegDir)) { Exit-Usage '-FfmpegDir must be an absolute folder path' }
if ($Stamp -and $Stamp -cnotmatch '^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$') {
    Exit-Usage '-Stamp must be yyyyMMdd-HHmmss-xxxx'
}
if ($WaitMinutes -and $WaitMinutes -cnotmatch '^[0-9]{1,2}$') { Exit-Usage '-WaitMinutes must be 0..60' }
if ($LaunchedBy -and $LaunchedBy -cne 'control-center' -and $LaunchedBy -cne 'script') {
    Exit-Usage '-LaunchedBy must be control-center or script'
}
if (-not (Test-VenvPython $Python)) { Exit-Usage '-Python must be an existing .venv\Scripts\python.exe' }

# env.ps1 points PYTHONPATH at this checkout's src, so this checkout's code runs (never -I or -E:
# they would ignore PYTHONPATH and run the venv's own copy instead).
. (Join-Path $PSScriptRoot 'env.ps1')

$Arguments = @('-m', 'biliflow.hardware_profile', 'measure', '--project-root', $ProjectRoot)
foreach ($Guard in $GuardRoots) { $Arguments += @('--guard-root', $Guard) }
if ($FfmpegDir) { $Arguments += @('--ffmpeg-dir', $FfmpegDir) }
if ($Stamp) { $Arguments += @('--stamp', $Stamp) }
if ($WaitMinutes) { $Arguments += @('--wait-minutes', $WaitMinutes) }
if ($LaunchedBy) { $Arguments += @('--launched-by', $LaunchedBy) }
& $Python @Arguments
exit $LASTEXITCODE
