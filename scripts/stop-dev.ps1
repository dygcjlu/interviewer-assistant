# Stop dev backend/frontend by listening ports.
# Usage: .\scripts\stop-dev.ps1

$ErrorActionPreference = "SilentlyContinue"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Get-DotEnvPort {
    $defaultPort = 8001
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) { return $defaultPort }
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*PORT\s*=\s*(\d+)') { return [int]$Matches[1] }
    }
    return $defaultPort
}

function Stop-PortListeners {
    param([int[]]$Ports)
    $killed = 0
    foreach ($port in $Ports) {
        $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        foreach ($conn in $conns) {
            $procId = $conn.OwningProcess
            if ($procId -and $procId -ne 0) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                Write-Host "Stopped PID $procId on port $port"
                $killed++
            }
        }
    }
    return $killed
}

$backendPort = Get-DotEnvPort

Write-Host "Stopping backend..."
$n = Stop-PortListeners -Ports @($backendPort)
if ($n -eq 0) {
    Write-Host "No listener on port $backendPort."
} else {
    Write-Host "Done." -ForegroundColor Green
}
