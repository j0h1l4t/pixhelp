param(
    [string]$Python = ""   # empty = resolve python.exe from PATH
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $Python) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "python.exe not on PATH; pass -Python <path> to override."
    }
    $Python = $cmd.Source
}

if (-not (Test-Path $Python)) {
    throw "Python not found at $Python. Pass -Python <path> to override."
}

Write-Host ">> Creating venv .\env with $Python"
& $Python -m venv env
$py = ".\env\Scripts\python.exe"

& $py -m pip install -q --upgrade pip
& $py -m pip install -q -r requirements.txt
& $py -m pip install -q -r requirements-dev.txt

Write-Host ""
Write-Host ">> Done. Run the backend with:"
Write-Host "   $py -m pixhelp"
Write-Host "Tests (pure logic, also runnable under WSL python3):"
Write-Host "   $py -m pytest tests"
Write-Host "Config: .\config.yaml (invalid keys are fatal at startup)"
