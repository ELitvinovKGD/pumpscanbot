$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    Write-Host "Virtual environment is missing. Run .\setup.ps1 first."
    exit 1
}

.\.venv\Scripts\python.exe -m app.main
