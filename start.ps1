param([int]$Port = 8000, [switch]$LocalOnly)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $LocalOnly) {
    try {
        $existingApp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/" -TimeoutSec 2
        if ($existingApp.application -eq 'supplypilot' -and $existingApp.status -eq 'ok') {
            Write-Host "SupplyPilot is already running at http://127.0.0.1:$Port"
            Write-Host 'Demo credentials: .demo-credentials.txt'
            return
        }
    } catch { }
}
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($LocalOnly) { $env:DATABASE_URL = 'sqlite' }
if (-not $LocalOnly -and (Test-Path -LiteralPath '.env')) {
    $databaseSetting = Get-Content -LiteralPath '.env' | Where-Object { $_ -match '^DATABASE_URL=postgresql://.+@127\.0\.0\.1:5433/' }
    if ($databaseSetting) {
        docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $dockerDesktopPath = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
            if (Test-Path -LiteralPath $dockerDesktopPath) {
                Start-Process -FilePath $dockerDesktopPath -WindowStyle Hidden
                Write-Host 'Starting Docker for PostgreSQL...'
                for ($attempt = 0; $attempt -lt 24; $attempt++) {
                    Start-Sleep -Seconds 5
                    docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
                    if ($LASTEXITCODE -eq 0) { break }
                }
            }
        }
        docker compose up -d --wait db
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed. Try .\start.ps1 -LocalOnly for the separate SQLite demo.' }
    }
}
if (-not (Test-Path -LiteralPath $projectPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        python -m pip --python $projectPython install pip
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment setup failed. See README.md.' }
    }
}
& $projectPython -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $projectPython manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Database migrations failed.' }
& $projectPython manage.py seed_demo
if ($LASTEXITCODE -ne 0) { throw 'Demo initialization failed.' }
& $projectPython manage.py collectstatic --noinput
if ($LASTEXITCODE -ne 0) { throw 'Static file collection failed.' }
Write-Host "SupplyPilot: http://127.0.0.1:$Port"
Write-Host 'Demo credentials: .demo-credentials.txt in the project folder.'
& $projectPython -m waitress --threads=8 --listen="127.0.0.1:$Port" supplypilot.wsgi:application

