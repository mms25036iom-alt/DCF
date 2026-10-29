$ErrorActionPreference = 'Stop'

Push-Location $PSScriptRoot
try {
    & uv run --locked ruff check --fix .
    if ($LASTEXITCODE -ne 0) {
        throw 'Ruff found errors that require manual fixes.'
    }
    Push-Location (Join-Path $PSScriptRoot 'apps/web')
    try {
        & npm.cmd run lint -- --fix
        if ($LASTEXITCODE -ne 0) {
            throw 'ESLint found errors that require manual fixes.'
        }
    }
    finally {
        Pop-Location
    }
    & (Join-Path $PSScriptRoot 'test.ps1')
}
finally {
    Pop-Location
}
