# Stops adk web (port 8080) and the dashboard (port 8000), and turns LangSmith tracing off.
# Run from the project folder:  .\stop_all.ps1
# Pass -KeepLangSmith to leave tracing on.

param(
    [switch]$KeepLangSmith
)

$ErrorActionPreference = "Continue"

function Stop-PortOwner {
    param([int]$Port, [string]$Label)

    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $connections) {
        Write-Host "[--] $Label is not running on port $Port"
        return
    }

    $processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($processId in $processIds) {
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        $name = if ($proc) { $proc.ProcessName } else { "unknown" }
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Stopped $Label on port $Port (process $name, PID $processId)" -ForegroundColor Green
    }
}

Write-Host "Stopping services" -ForegroundColor Cyan
Stop-PortOwner -Port 8080 -Label "adk web"
Stop-PortOwner -Port 8000 -Label "dashboard"

if ($KeepLangSmith) {
    Write-Host "[--] LangSmith tracing left on (-KeepLangSmith)"
} else {
    Write-Host "Turning LangSmith tracing off" -ForegroundColor Cyan
    # Only the on/off flag changes. The API key and project name are kept.
    [Environment]::SetEnvironmentVariable("LANGSMITH_TRACING", "false", "User")
    $env:LANGSMITH_TRACING = "false"
    Write-Host "[OK] LANGSMITH_TRACING set to false for this Windows user"
}

Write-Host ""
Write-Host "Done. Close any leftover PowerShell windows that ran adk or the dashboard."
