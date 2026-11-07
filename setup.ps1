param(
    [switch]$CodeOnly
)

$ErrorActionPreference = "Stop"

Write-Host "Creating virtual environment..."
python -m venv .venv

Write-Host "Installing dependencies..."
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

if (-not $CodeOnly -and -not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Created .env from .env.example. Open .env and fill your keys."
}

if ($CodeOnly) {
    Write-Host "Done. Code-only environment is ready for tests. The bot remains on the server."
} else {
    Write-Host "Done. Fill .env, start ngrok/cloudflared, then run .\run.ps1"
}
