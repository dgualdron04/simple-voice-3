$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

if (!(Test-Path ".\.venv")) {
    Write-Host "No existe .venv. Ejecuta primero:" -ForegroundColor Red
    Write-Host "powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1"
    exit 1
}

& ".\.venv\Scripts\Activate.ps1"

python -m src.assistant_voice