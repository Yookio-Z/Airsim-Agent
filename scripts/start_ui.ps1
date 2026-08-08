param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8765,
    [ValidateSet("px4_mavlink", "airsim")]
    [string]$Backend = "px4_mavlink"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found: $python"
}

Set-Location -LiteralPath $repoRoot
& $python -m src.ui.server --host $HostAddress --port $Port --backend $Backend
if ($LASTEXITCODE -ne 0) {
    throw "UI server exited with code $LASTEXITCODE"
}
