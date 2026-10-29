# Run the project-local Graphify CLI from any working directory.
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$GraphifyExecutable = Join-Path $ProjectRoot '.tools\graphify\Scripts\graphify.exe'

if (-not (Test-Path -LiteralPath $GraphifyExecutable)) {
    throw 'Graphify is not installed. See docs/GRAPHIFY.md for installation commands.'
}

$PreviousPythonUtf8 = $env:PYTHONUTF8
$env:PYTHONUTF8 = '1'
Push-Location -LiteralPath $ProjectRoot
try {
    & $GraphifyExecutable @args
    $GraphifyExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
    $env:PYTHONUTF8 = $PreviousPythonUtf8
}
exit $GraphifyExitCode
