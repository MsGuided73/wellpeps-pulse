# Local LIVE dashboard: real collected posts in data/live.db (never the demo data).
#
#   .\scripts\run_live.ps1               # dashboard on http://127.0.0.1:5556, signed in automatically
#   .\scripts\run_live.ps1 -Pull         # first pull new Reddit posts through Apify (paid, capped by
#                                        #   harvey.yaml collectors.apify_reddit), then open the dashboard
#   .\scripts\run_live.ps1 -Port 5560
#
# Real config (config/), real claims: nothing is marked approved for demonstration.
# Claude calls bill the logged-in subscription (PULSE_CLAUDE_BILLING=subscription).
# Pulse never posts anything; replies are drafted for a human.
param(
    [switch]$Pull,
    [int]$Port = 5556
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:PULSE_DB_PATH = "data/live.db"
$env:PULSE_DEV_NO_AUTH = "true"
$env:PULSE_CLAUDE_BILLING = "subscription"
Remove-Item Env:PULSE_DEMO_SANDBOX -ErrorAction SilentlyContinue
Remove-Item Env:PULSE_CONFIG_DIR -ErrorAction SilentlyContinue

if ($Pull) {
    & .venv\Scripts\python.exe -m harvey ingest --apify-reddit
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& .venv\Scripts\python.exe -m harvey dashboard --port $Port
