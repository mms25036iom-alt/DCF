param([switch]$Stop, [switch]$Restart, [switch]$Development)
$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$runtimeRoot = Join-Path $repoRoot '.private/runtime'
$logRoot = Join-Path $runtimeRoot 'logs'
$processFile = Join-Path $runtimeRoot 'processes.json'
$python = Join-Path $repoRoot '.venv/Scripts/python.exe'
$webRoot = Join-Path $repoRoot 'apps/web'
function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
function Get-RecordedProcess {
    param($Record)
    $process = Get-Process -Id $Record.id -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().Ticks.ToString() -eq $Record.started) { return $process }
}
function Test-Port {
    param([int]$Port)
    $client = [Net.Sockets.TcpClient]::new()
    try { return $client.ConnectAsync('127.0.0.1', $Port).Wait(500) -and $client.Connected }
    catch { return $false }
    finally { $client.Dispose() }
}
function Wait-Http {
    param([string]$Url)
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return }
        }
        catch { Start-Sleep -Milliseconds 400 }
    }
    throw "Startup did not become ready at $Url. Inspect the private runtime logs."
}
function Start-Component {
    param([string]$Name, [string]$Program, [string[]]$Arguments, [string]$Directory, [int]$Port)
    $record = @($script:records | Where-Object { $_.name -eq $Name }) | Select-Object -First 1
    if ($record -and (Get-RecordedProcess $record)) { Write-Host "$Name is already running."; return }
    if (Test-Port $Port) { throw "Port $Port is already in use. Stop its service first." }
    $argumentLine = ($Arguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
    $process = Start-Process -FilePath $Program -ArgumentList $argumentLine -WorkingDirectory $Directory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot "$Name.log") -RedirectStandardError (Join-Path $logRoot "$Name-error.log")
    $script:records = @($script:records | Where-Object { $_.name -ne $Name }) + @(@{ name=$Name; id=$process.Id; started=$process.StartTime.ToUniversalTime().Ticks.ToString() })
    ConvertTo-Json -InputObject @($script:records) | Set-Content -LiteralPath $processFile -Encoding utf8
    Write-Host "Started $Name (PID $($process.Id))."
}
Push-Location $repoRoot
try {
    New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
    $script:records = if (Test-Path -LiteralPath $processFile) { @(ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($processFile))) } else { @() }
    if ($Stop -or $Restart) {
        foreach ($record in $script:records) { if (Get-RecordedProcess $record) { & taskkill.exe /PID $record.id /T /F | Out-Null } }
        $script:records = @()
        Set-Content -LiteralPath $processFile -Value '[]' -Encoding utf8
        if ($Stop -and -not $Restart) { Write-Host 'Stopped recorded project processes.'; return }
    }
    if (-not (Test-Path -LiteralPath $python)) { Invoke-Checked 'uv' @('sync','--locked') }
    Invoke-Checked $python @('scripts/generate_secrets.py','--local')
    foreach ($line in [IO.File]::ReadAllLines((Join-Path $runtimeRoot 'local-secrets.env'))) {
        if ($line -match '^([A-Z_]+)=(.+)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
    }
    $env:APP_MODE = 'LIVE'
    $env:APP_ENV = 'development'
    $env:ARTIFACT_STORAGE_ROOT = Join-Path $repoRoot '.private/public-cache'
    $env:API_BASE_URL = 'http://127.0.0.1:8000'
    $env:NEXT_TELEMETRY_DISABLED = '1'
    if (-not (Test-Path -LiteralPath (Join-Path $webRoot 'node_modules/next/package.json'))) {
        Push-Location $webRoot
        try { Invoke-Checked 'npm.cmd' @('ci') } finally { Pop-Location }
    }
    if (-not $Development -and -not (Test-Path -LiteralPath (Join-Path $webRoot '.next/BUILD_ID'))) {
        Push-Location $webRoot
        try { Invoke-Checked 'npm.cmd' @('run','build') } finally { Pop-Location }
    }
    Start-Component 'api' $python @('-m','uvicorn','services.serverless.app:app','--host','127.0.0.1','--port','8000','--no-access-log') $repoRoot 8000
    $node = (Get-Command node.exe -ErrorAction Stop).Source
    $next = Join-Path $webRoot 'node_modules/next/dist/bin/next'
    $mode = if ($Development) { 'dev' } else { 'start' }
    Start-Component 'web' $node @($next,$mode,'--hostname','127.0.0.1','--port','3000') $webRoot 3000
    Wait-Http 'http://127.0.0.1:8000/health'
    Wait-Http 'http://127.0.0.1:3000/models'
    Write-Host 'Open http://127.0.0.1:3000/models . No database or Redis is required.'
}
finally { Pop-Location }
