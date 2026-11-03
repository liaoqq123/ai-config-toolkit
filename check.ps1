$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    & python -X utf8 -m unittest discover -s tests -v
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
