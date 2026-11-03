param(
    [string]$Project = 'config/project.example.json',
    [string]$Root = 'demo',
    [int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    & python -X utf8 client/review_server.py --project $Project --root $Root --port $Port
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
