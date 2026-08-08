param(
    [string]$Distro = "Ubuntu",
    [string]$HostAddress = "127.0.0.1",
    [int]$UiPort = 8765,
    [int]$GatewayPort = 8766,
    [string]$Backend = "px4_ros2",
    [string]$WslRepoRoot = "/mnt/c/Users/26494/Desktop/airsim_agent",
    [switch]$NoGateway,
    [switch]$GatewayOnly
)

$ErrorActionPreference = "Stop"

function Invoke-WslScript {
    param([string]$Script)
    $tempPath = Join-Path $env:TEMP ("airsim_agent_wsl_" + [guid]::NewGuid().ToString("N") + ".sh")
    $lfScript = ($Script -replace "`r`n", "`n") -replace "`r", "`n"
    [System.IO.File]::WriteAllText($tempPath, $lfScript, [System.Text.UTF8Encoding]::new($false))
    try {
        $portableTempPath = $tempPath -replace "\\", "/"
        $wslScript = ((wsl.exe -d $Distro -- wslpath -a $portableTempPath) | Select-Object -First 1).Trim()
        wsl.exe -d $Distro -- bash $wslScript
    } finally {
        Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
    }
}

function Test-HttpJson {
    param([string]$Url)
    try {
        Invoke-RestMethod -Uri $Url -TimeoutSec 1 | Out-Null
        return $true
    } catch {
        return $false
    }
}

$gatewayUrl = "http://127.0.0.1:$GatewayPort"
$gatewayPidFile = "/tmp/airsim_agent_ros_gateway_$GatewayPort.pid"
$gatewayLogFile = "/tmp/airsim_agent_ros_gateway_$GatewayPort.log"
$startedGateway = $false
$gatewayProcess = $null

if (-not $NoGateway) {
    if (-not (Test-HttpJson "$gatewayUrl/health")) {
        $gatewayCommand = "cd '$WslRepoRoot' && exec env PORT='$GatewayPort' REPO_ROOT='$WslRepoRoot' bash '$WslRepoRoot/scripts/start_ros_gateway_wsl.sh' >'$gatewayLogFile' 2>&1"
        $gatewayProcess = Start-Process -FilePath "wsl.exe" -ArgumentList @("-d", $Distro, "--", "bash", "-lc", $gatewayCommand) -WindowStyle Hidden -PassThru
        $startedGateway = $true
    }

    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        if (Test-HttpJson "$gatewayUrl/health") {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        Write-Host "ROS gateway did not answer at $gatewayUrl/health"
        Invoke-WslScript "tail -80 '$gatewayLogFile' 2>/dev/null || true"
        exit 1
    }
    Write-Host "ROS gateway ready at $gatewayUrl"
    if ($GatewayOnly) {
        Invoke-RestMethod -Uri "$gatewayUrl/health" -TimeoutSec 2 | ConvertTo-Json -Depth 8
        exit 0
    }
}

$env:AIRSIM_AGENT_BACKEND = $Backend
$env:AIRSIM_AGENT_ROS_BRIDGE_URL = $gatewayUrl

try {
    uv run python -m src.ui.server --host $HostAddress --port $UiPort --backend $Backend
} finally {
    if ($startedGateway) {
        if ($gatewayProcess -ne $null) {
            Stop-Process -Id $gatewayProcess.Id -Force -ErrorAction SilentlyContinue
        }
        $stopGateway = @"
pkill -f 'ros2 run airsim_agent_ros gateway_node' || true
pkill -f 'start_ros_gateway_wsl.sh' || true
"@
        Invoke-WslScript $stopGateway | Out-Null
    }
}
