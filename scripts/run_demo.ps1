# Local client demo: Pulse + the DEMO sandbox, signed in automatically.
#
#   .\scripts\run_demo.ps1                 # reuse the existing demo data
#   .\scripts\run_demo.ps1 -Seed           # (re)build data/demo.db, data/sandbox.db and data/demo-config first
#   .\scripts\run_demo.ps1 -Seed -Claude   # ... with REAL Claude (claude CLI) for the hand-written demo posts
#                                          #     and the briefs; synthetic history stays the deterministic fake
#   .\scripts\run_demo.ps1 -Port 5566      # another port (sandbox links follow the port you open)
#
# DEMO ONLY: claims in data/demo-config are marked approved for demonstration
# (not signed off) and every post link opens a fictional thread on this machine.
# Never use these settings for real replies, and never in docker-compose/Coolify.
param(
    [switch]$Seed,
    [switch]$Claude,
    [int]$Port = 5555
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:PULSE_DB_PATH = "data/demo.db"
$env:PULSE_DEMO_SANDBOX = "true"
$env:PULSE_DEV_NO_AUTH = "true"
$env:PULSE_SANDBOX_DB_PATH = "data/sandbox.db"
$env:PULSE_CONFIG_DIR = "data/demo-config"

if ($Seed) {
    # The sandbox seed needs a fresh database: keep the old one as a backup.
    if (Test-Path "data/demo.db") {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        Move-Item "data/demo.db" "data/demo.db.bak-$stamp"
        Write-Host "  previous data/demo.db kept as data/demo.db.bak-$stamp"
    }
    $seedArgs = @("scripts\seed_demo.py", "--sandbox", "--base-url", "http://127.0.0.1:$Port")
    if ($Claude) {
        # Real Claude calls through the logged-in claude CLI, at most 3 at a time.
        $seedArgs += @("--claude", "--review-sheet", "data/demo-review-sheet.md")
    }
    & .venv\Scripts\python.exe @seedArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& .venv\Scripts\python.exe -m harvey dashboard --port $Port
