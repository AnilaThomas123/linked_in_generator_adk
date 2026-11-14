# Turns LangSmith tracing on for this Windows user account.
# Run from PowerShell:  .\enable_langsmith.ps1
# The API key is typed in hidden and is never printed or written to a file.

$ErrorActionPreference = "Stop"

Write-Host "LangSmith tracing setup" -ForegroundColor Cyan

$secureKey = Read-Host "Paste your LangSmith API key (input hidden)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if ([string]::IsNullOrWhiteSpace($apiKey)) {
    Write-Host "No API key entered. Nothing changed." -ForegroundColor Red
    exit 1
}

$project = Read-Host "LangSmith project name [linked-in-generator]"
if ([string]::IsNullOrWhiteSpace($project)) {
    $project = "linked-in-generator"
}

$settings = @{
    "LANGSMITH_TRACING" = "true"
    "LANGSMITH_API_KEY" = $apiKey
    "LANGSMITH_PROJECT" = $project
}

foreach ($name in $settings.Keys) {
    # Saved for this Windows user, so new terminals and adk web pick it up.
    [Environment]::SetEnvironmentVariable($name, $settings[$name], "User")
    # Also set for the current window.
    Set-Item -Path "Env:$name" -Value $settings[$name]
}

Write-Host ""
Write-Host "Tracing is on." -ForegroundColor Green
Write-Host "  LANGSMITH_TRACING = true"
Write-Host "  LANGSMITH_API_KEY = (saved, hidden)"
Write-Host "  LANGSMITH_PROJECT = $project"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Close any open terminals and open a new one (existing ones keep old values)."
Write-Host "  2. Restart adk web if it is running."
Write-Host "  3. Run: python check_langsmith.py"
