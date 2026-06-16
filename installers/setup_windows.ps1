Write-Host "======================================" -ForegroundColor Cyan
Write-Host " Instalador ZUU - Windows" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

Write-Host "[1/5] Verificando Python..." -ForegroundColor Yellow

$pythonCmd = $null

try {
    py -3 --version | Out-Null
    $pythonCmd = "py -3"
} catch {
    try {
        python --version | Out-Null
        $pythonCmd = "python"
    } catch {
        Write-Host "Python no está instalado o no está en el PATH." -ForegroundColor Red
        Write-Host "Instala Python y marca la opción: Add Python to PATH."
        exit 1
    }
}

Write-Host "Python detectado usando: $pythonCmd" -ForegroundColor Green

Write-Host "[2/5] Creando entorno virtual..." -ForegroundColor Yellow

if (!(Test-Path ".venv")) {
    Invoke-Expression "$pythonCmd -m venv .venv"
} else {
    Write-Host "El entorno .venv ya existe." -ForegroundColor Green
}

Write-Host "[3/5] Activando entorno virtual..." -ForegroundColor Yellow

$ActivatePath = ".\.venv\Scripts\Activate.ps1"

if (!(Test-Path $ActivatePath)) {
    Write-Host "No se encontró el activador del entorno virtual." -ForegroundColor Red
    exit 1
}

& $ActivatePath

Write-Host "[4/5] Actualizando pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip setuptools wheel

Write-Host "[5/5] Instalando librerías..." -ForegroundColor Yellow
pip install -r requirements.txt

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host " Instalación completada" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Para ejecutar:" -ForegroundColor Yellow
Write-Host ".\.venv\Scripts\Activate.ps1"
Write-Host "python -m src.assistant_voice"