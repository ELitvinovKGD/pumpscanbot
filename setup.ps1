$ErrorActionPreference = "Stop"

Write-Host "Creating virtual environment..."
python -m venv .venv

Write-Host "Installing dependencies..."
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Created .env from .env.example. Open .env and fill your keys."
}

Write-Host "Done. Fill .env, start ngrok/cloudflared, then run .\run.ps1"
