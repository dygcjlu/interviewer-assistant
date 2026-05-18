# Start backend + frontend for local development.
# Usage: .\scripts\start-dev.ps1

[CmdletBinding()]
param(
    [string]$CondaEnv = "interview-assistant",
    [switch]$SkipFrontend,
    [switch]$SkipBackend,
    [switch]$InstallDeps
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendDir = Join-Path $Root "frontend"
$LogsDir = Join-Path $Root "logs"
$null = New-Item -ItemType Directory -Force -Path $LogsDir

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory)][string]$Key,
        [string]$Default = ""
    )
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) { return $Default }
    foreach ($line in Get-Content $envFile) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=\s*(.+?)\s*$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $Default
}

function Resolve-PythonExe {
    $candidates = @(
        (Join-Path $Root ".venv\Scripts\python.exe"),
        (Join-Path $env:USERPROFILE ".conda\envs\$CondaEnv\python.exe"),
        (Join-Path $env:LOCALAPPDATA "conda\conda\envs\$CondaEnv\python.exe")
    )
    if ($env:CONDA_PREFIX -and $env:CONDA_DEFAULT_ENV -eq $CondaEnv) {
        $candidates = @((Join-Path $env:CONDA_PREFIX "python.exe")) + $candidates
    }
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) { return (Resolve-Path $path).Path }
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    return $null
}

function Test-CommandExists {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Wait-BackendReady {
    param(
        [string]$BaseUrl,
        [int]$TimeoutSec = 60
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri "$BaseUrl/api/session/current" -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) { return $true }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

$HostAddr = Get-DotEnvValue -Key "HOST" -Default "127.0.0.1"
$Port = Get-DotEnvValue -Key "PORT" -Default "8001"
$BackendUrl = "http://${HostAddr}:$Port"

Write-Host ""
Write-Host "=== Interview Assistant (dev) ===" -ForegroundColor Cyan
Write-Host "Root:    $Root"
Write-Host "Backend: $BackendUrl"
Write-Host ""

if ((Test-Path (Join-Path $FrontendDir "vite.config.js")) -and ($Port -ne "8001")) {
    Write-Host "WARN: .env PORT=$Port but vite.config.js proxies to 8001." -ForegroundColor Yellow
    Write-Host "      Update frontend/vite.config.js if the UI cannot reach the API."
    Write-Host ""
}

if (-not $SkipBackend) {
    $python = Resolve-PythonExe
    if (-not $python) {
        throw "Python not found. Create .venv or conda env '$CondaEnv' first."
    }
    Write-Host "Python: $python"

    if ($InstallDeps) {
        Write-Host "Installing Python dependencies..."
        & $python -m pip install -r (Join-Path $Root "requirements.txt")
    }

    $backendOut = Join-Path $LogsDir "backend.out.log"
    $backendErr = Join-Path $LogsDir "backend.err.log"

    Write-Host "Starting backend..."
    Start-Process -FilePath $python `
        -ArgumentList "-m", "src.main" `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $backendOut `
        -RedirectStandardError $backendErr | Out-Null

    Write-Host "Waiting for backend..."
    if (-not (Wait-BackendReady -BaseUrl $BackendUrl)) {
        Write-Host "Backend failed to start. Last log lines:" -ForegroundColor Red
        if (Test-Path $backendErr) { Get-Content $backendErr -Tail 20 }
        throw "Backend not ready. See $backendErr"
    }
    Write-Host "Backend is up." -ForegroundColor Green
}

if (-not $SkipFrontend) {
    if (-not (Test-CommandExists "npm")) {
        throw "npm not found. Install Node.js first."
    }

    if ($InstallDeps -or -not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
        Write-Host "Running npm install..."
        Push-Location $FrontendDir
        try {
            npm install
        } finally {
            Pop-Location
        }
    }

    Write-Host "Starting frontend (Vite)..."
    # npm on Windows is a .cmd shim; run via cmd.exe for Start-Process compatibility
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c", "npm run dev" `
        -WorkingDirectory $FrontendDir `
        -WindowStyle Normal | Out-Null

    Write-Host "Frontend started in a new console window." -ForegroundColor Green
}

Write-Host ""
Write-Host "Open in browser:" -ForegroundColor Cyan
Write-Host "  http://localhost:5173  (or 5174 if 5173 is busy - check Vite window)"
Write-Host "  API: $BackendUrl/api/session/current"
Write-Host "  Logs: $LogsDir"
Write-Host "        app logs: logs/app.log, logs/app.error.log (application)"
Write-Host "        process:  logs/backend.out.log, logs/backend.err.log (stdout/stderr redirect)"
Write-Host ""
Write-Host "Stop: .\scripts\stop-dev.ps1" -ForegroundColor DarkGray
Write-Host ""
