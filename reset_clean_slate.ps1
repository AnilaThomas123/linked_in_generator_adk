# reset_clean_slate.ps1
# Deletes everything the agent generated so the demo starts from a clean slate:
#   drafts, ready_to_post posts, infographics, published PDFs, research sources,
#   pipeline output, the draft database, the audit log and ADK session history.
# Agent code, config, tests, .env, .venv and the start/stop scripts are NOT touched.
#
# Usage (from the project folder):   .\reset_clean_slate.ps1

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location -LiteralPath $root

# 1. Refuse to run while the app is running, so it cannot delete files out from under it.
$running = Get-NetTCPConnection -State Listen -LocalPort 8000, 8080 -ErrorAction SilentlyContinue
if ($running) {
    Write-Host ''
    Write-Host 'The app is still running (port 8000 or 8080 is in use).' -ForegroundColor Yellow
    Write-Host 'Stop it first with:  .\stop_all.ps1' -ForegroundColor Yellow
    Write-Host 'Then run this script again.' -ForegroundColor Yellow
    exit 1
}

# 2. Build the list of generated items that exist right now.
$folders = @('drafts', 'ready_to_post', 'infographics', 'published_pdfs', 'sources', 'data\adk_workflow', 'data\replay')
$files = @('reports\editorial_audit.csv', 'app\.adk\session.db')
$dbFiles = Get-ChildItem -LiteralPath (Join-Path $root 'data') -Filter 'editorial.db*' -File -ErrorAction SilentlyContinue |
    ForEach-Object { 'data\' + $_.Name }

$items = @()
foreach ($f in $folders + $files + $dbFiles) {
    $path = Join-Path $root $f
    if (Test-Path -LiteralPath $path) { $items += $f }
}

Write-Host ''
Write-Host 'Agent-generated data that will be DELETED:' -ForegroundColor Cyan
if ($items.Count -eq 0) {
    Write-Host '  (nothing to delete - the project is already clean)'
    exit 0
}
foreach ($i in $items) { Write-Host ('  - ' + $i) }
Write-Host ''
Write-Host 'Agent code, config, tests, .env and .venv will NOT be touched.' -ForegroundColor Green
Write-Host 'Deleted files cannot be recovered.' -ForegroundColor Yellow
Write-Host ''

# 3. Explicit confirmation.
$answer = Read-Host 'Type RESET to delete this data, or press Enter to cancel'
if ($answer -cne 'RESET') {
    Write-Host 'Cancelled. Nothing was deleted.' -ForegroundColor Green
    exit 0
}

# 4. Delete, then recreate the empty folders the app writes into.
foreach ($i in $items) {
    Remove-Item -LiteralPath (Join-Path $root $i) -Recurse -Force
    Write-Host ('Deleted: ' + $i)
}
foreach ($d in @('drafts', 'ready_to_post', 'infographics', 'published_pdfs', 'sources', 'reports')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root $d) | Out-Null
}

Write-Host ''
Write-Host 'Clean slate ready. Start the app with .\start_all.ps1' -ForegroundColor Green
