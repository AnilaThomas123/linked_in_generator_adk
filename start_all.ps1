# Starts adk web and the dashboard, then runs the service and LangSmith check.
# Run from the project folder:  .\start_all.ps1

$ErrorActionPreference = "Stop"
$projectDir = $PSScriptRoot
Set-Location $projectDir

Write-Host "1. Activating virtual environment" -ForegroundColor Cyan
$venvActivate = $null
foreach ($name in @(".venv", "venv")) {
    $candidate = Join-Path $projectDir "$name\Scripts\Activate.ps1"
    if (Test-Path $candidate) { $venvActivate = $candidate; break }
}
if (-not $venvActivate) {
    Write-Host "No .venv or venv folder with Scripts\Activate.ps1 found. Create one first:" -ForegroundColor Red
    Write-Host "  python -m venv .venv"
    Write-Host "  .\.venv\Scripts\Activate.ps1"
    Write-Host '  pip install -e ".[dev,adk,observability]"'
    exit 1
}
. $venvActivate
Write-Host "   Activated: $venvActivate"

Write-Host "2. Checking google-adk is installed" -ForegroundColor Cyan
pip show google-adk *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "   Not installed. Installing google-adk[openai]..."
    pip install "google-adk[openai]>=2.10.0,<3.0.0"
    if ($LASTEXITCODE -ne 0) { Write-Host "Install failed." -ForegroundColor Red; exit 1 }
}
if (-not (Get-Command adk -ErrorAction SilentlyContinue)) {
    Write-Host "'adk' command still not found after install. Paste this script's output to get help." -ForegroundColor Red
    exit 1
}
Write-Host "   adk is available"

Write-Host "3. Checking LangSmith settings" -ForegroundColor Cyan
if (-not $env:LANGSMITH_TRACING) {
    Write-Host "   LANGSMITH_TRACING not set in this window. Run .\enable_langsmith.ps1 first, then open a new window." -ForegroundColor Yellow
} else {
    Write-Host "   LANGSMITH_TRACING=$($env:LANGSMITH_TRACING)"
}

# Keep the venv active in the new windows too.
$activateCmd = "& '$venvActivate'; Set-Location '$projectDir'"

Write-Host "4. Starting adk web on port 8080 in a new window" -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "$activateCmd; adk web --port 8080"

Write-Host "5. Starting dashboard on port 8000 in a new window" -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "$activateCmd; python -m app.cli dashboard"

Write-Host "6. Waiting for servers to start (15 seconds)"
Start-Sleep -Seconds 15

Write-Host "7. Running service and LangSmith checks" -ForegroundColor Cyan
python check_services.py
