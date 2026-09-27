param(
    [ValidateSet('after_stage', 'immediate')]
    [string]$Mode = 'after_stage'
)

$BiliflowRoot = Split-Path -Parent $PSScriptRoot
$StatePath = Join-Path $BiliflowRoot 'state\control-center.json'
if (-not (Test-Path -LiteralPath $StatePath)) {
    Write-Host 'BiliFlow Control Center is not running.'
    exit 0
}

try {
    $State = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    $Headers = @{ 'X-BiliFlow-Token' = [string]$State.token }
    $Body = @{ mode = $Mode } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri ($State.url + 'api/shutdown') -Method Post `
        -Headers $Headers -ContentType 'application/json' -Body $Body -TimeoutSec 10 | Out-Null
    Write-Host "BiliFlow received shutdown mode: $Mode"
} catch {
    throw "Could not stop BiliFlow cleanly: $($_.Exception.Message)"
}
