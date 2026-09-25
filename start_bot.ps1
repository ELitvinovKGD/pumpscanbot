$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Write-Step([string]$Text) {
    Write-Host "[PumpScanBot] $Text" -ForegroundColor Cyan
}

function Get-EnvValue([string]$Name, [string]$Default = "") {
    $envPath = Join-Path $PSScriptRoot ".env"
    foreach ($line in [System.IO.File]::ReadAllLines($envPath)) {
        if ($line -match "^$([regex]::Escape($Name))=(.*)$") {
            return $Matches[1].Trim()
        }
    }
    return $Default
}

function Set-EnvValue([string]$Name, [string]$Value) {
    $envPath = Join-Path $PSScriptRoot ".env"
    $content = [System.IO.File]::ReadAllText($envPath)
    $pattern = "(?m)^$([regex]::Escape($Name))=.*$"
    $newLine = "$Name=$Value"

    if ([regex]::IsMatch($content, $pattern)) {
        $content = [regex]::Replace($content, $pattern, $newLine, 1)
    } else {
        $content = $content.TrimEnd() + [Environment]::NewLine + $newLine + [Environment]::NewLine
    }

    [System.IO.File]::WriteAllText($envPath, $content, [System.Text.UTF8Encoding]::new($false))
}

if (-not (Test-Path -LiteralPath ".env")) {
    throw ".env was not found. Run setup.ps1 and fill in the keys first."
}

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "Python virtual environment was not found. Run setup.ps1 first."
}

$port = Get-EnvValue "PORT" "8080"
$localHealthUrl = "http://127.0.0.1:$port/health"

try {
    $alreadyRunning = Invoke-RestMethod -Uri $localHealthUrl -TimeoutSec 2
    if ($alreadyRunning.status -eq "ok") {
        Write-Host "The bot is already running on port $port." -ForegroundColor Yellow
        exit 0
    }
} catch {
    # This is expected before the bot starts.
}

Write-Step "Checking ngrok..."
$ngrokApiAvailable = $false
try {
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2
    $ngrokApiAvailable = $true
    Write-Step "Using the existing ngrok process."
} catch {
    # ngrok is not running yet.
}

if (-not $ngrokApiAvailable) {
    $ngrokCommand = Get-Command "ngrok.exe" -ErrorAction SilentlyContinue
    if (-not $ngrokCommand) {
        $ngrokCommand = Get-Command "ngrok" -ErrorAction SilentlyContinue
    }

    $ngrokPath = if ($ngrokCommand) { $ngrokCommand.Source } else { Join-Path $PSScriptRoot "tools\ngrok.exe" }
    if (-not (Test-Path -LiteralPath $ngrokPath)) {
        throw "ngrok was not found. Add it to PATH or place ngrok.exe in the project tools folder."
    }

    Start-Process -FilePath $ngrokPath -ArgumentList @("http", $port) -WindowStyle Hidden | Out-Null
}

# The local API may start before the public tunnel is ready. Wait for the
# actual HTTPS tunnel for this bot instead of treating an empty API as ready.
$httpsTunnel = $null
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        $tunnels = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2
        $httpsTunnel = $tunnels.tunnels |
            Where-Object { $_.public_url -like "https://*" -and $_.config.addr -match ":$port/?$" } |
            Select-Object -First 1
        if ($httpsTunnel) {
            break
        }
    } catch {
        # Wait until both the local API and the HTTPS tunnel are ready.
    }
    Start-Sleep -Milliseconds 500
}

if (-not $httpsTunnel) {
    throw "ngrok did not create an HTTPS tunnel for port $port within 15 seconds."
}

$publicUrl = $httpsTunnel.public_url.TrimEnd("/")
$previousUrl = Get-EnvValue "PUBLIC_WEBHOOK_URL"
if ($previousUrl.TrimEnd("/") -ne $publicUrl) {
    Set-EnvValue "PUBLIC_WEBHOOK_URL" $publicUrl
    $env:PUMPSCAN_PUBLIC_URL_CHANGED = "1"
    Write-Host "New public URL: $publicUrl" -ForegroundColor Green
    Write-Step "The URL was updated in .env. Helius will sync once after startup."
} else {
    $env:PUMPSCAN_PUBLIC_URL_CHANGED = "0"
    Write-Host "Public URL is unchanged: $publicUrl" -ForegroundColor Green
    Write-Step "Helius sync is not needed. No Helius credits will be used."
}

Write-Step "Starting the bot. You may minimize this window, but do not close it."
Write-Host "Press Ctrl+C to stop the bot." -ForegroundColor DarkGray
& ".\.venv\Scripts\python.exe" -m app.main
