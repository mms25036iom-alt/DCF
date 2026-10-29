param([switch]$Build)

$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE"
    }
}

Push-Location $PSScriptRoot
try {
    $testRunsRoot = Join-Path $PSScriptRoot '.private/test-runs'
    New-Item -ItemType Directory -Path $testRunsRoot -Force | Out-Null
    Invoke-Checked 'uv' @('run', '--locked', 'ruff', 'check', '.')
    Invoke-Checked 'uv' @('run', '--locked', 'pytest', '-q', '--basetemp', (Join-Path $testRunsRoot ([Guid]::NewGuid().ToString('N'))))
    Push-Location (Join-Path $PSScriptRoot 'apps/web')
    try {
        Invoke-Checked 'npm.cmd' @('run', 'lint')
        Invoke-Checked 'npm.cmd' @('run', 'typecheck')
        Invoke-Checked 'npm.cmd' @('test')
        if ($Build) {
            Invoke-Checked 'npm.cmd' @('run', 'build')
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}
