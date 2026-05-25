# Start backend (NiceGUI frontend is embedded)
# Usage: .\scripts\start-dev.ps1

$ErrorActionPreference = "Stop"

$Root     = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Logs     = Join-Path $Root "logs"
$CondaEnv = if ($env:CONDA_ENV) { $env:CONDA_ENV } else { "interview-assistant" }

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

function Get-EnvValue([string]$Key, [string]$Fallback) {
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match "^\s*${Key}\s*=\s*(.+)$") {
                return $Matches[1].Trim().Trim('"').Trim("'")
            }
        }
    }
    return $Fallback
}

function Resolve-Python {
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }

    try {
        $condaPy = & conda run -n $CondaEnv python -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $condaPy -and (Test-Path $condaPy)) { return $condaPy }
    } catch {}

    if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }

    throw "Python not found. Create .venv or conda env '$CondaEnv'."
}

$hostAddr   = Get-EnvValue "HOST" "127.0.0.1"
$port       = Get-EnvValue "PORT" "8000"
$backendUrl = "http://${hostAddr}:${port}"
$python     = Resolve-Python

Write-Host ""
Write-Host "=== Interview Assistant Dev Start ==="
Write-Host "Root:    $Root"
Write-Host "Backend: $backendUrl"
Write-Host "Python:  $python"
Write-Host ""

Write-Host "Starting backend..."
$backendOut = Join-Path $Logs "backend.out.log"
$backendErr = Join-Path $Logs "backend.err.log"
$proc = Start-Process -FilePath $python `
    -ArgumentList "-m", "src.main" `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $backendOut `
    -RedirectStandardError  $backendErr `
    -PassThru -NoNewWindow
$proc.Id | Set-Content (Join-Path $Logs "backend.pid")

Write-Host "Waiting for backend..."
$healthUrl = "${backendUrl}/api/session/current"
for ($i = 0; $i -lt 60; $i++) {
    try {
        $req = [System.Net.HttpWebRequest]::Create($healthUrl)
        $req.Timeout = 1000
        $req.GetResponse().Close()
        Write-Host "Backend ready (PID $($proc.Id))" -ForegroundColor Green
        break
    } catch {}

    if ($proc.HasExited) {
        Write-Host "Backend exited unexpectedly. Last errors:" -ForegroundColor Red
        if (Test-Path $backendErr) { Get-Content $backendErr -Tail 20 }
        exit 1
    }
    Start-Sleep -Seconds 1
}

Write-Host ""
Write-Host "Open: $backendUrl"
Write-Host "Stop: .\scripts\stop-dev.ps1"
Write-Host ""
