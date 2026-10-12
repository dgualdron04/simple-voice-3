#!/usr/bin/env bash

set -e

echo "======================================"
echo " Instalador ZUU - Linux / WSL"
echo "======================================"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "[1/6] Verificando Python..."

if ! command -v python3 &> /dev/null; then
    echo "Python3 no está instalado."
    echo "Instálalo con:"
    echo "sudo apt update && sudo apt install python3 python3-venv python3-pip -y"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Python detectado: $PYTHON_VERSION"

echo "[2/6] Instalando dependencias del sistema..."

if command -v apt &> /dev/null; then
    sudo apt update
    sudo apt install -y python3-venv python3-pip portaudio19-dev libasound2-dev
else
    echo "No se detectó apt. Si no estás en Ubuntu/Debian, instala manualmente:"
    echo "- python3-venv"
    echo "- python3-pip"
    echo "- portaudio"
    echo "- alsa/libasound"
fi

echo "[3/6] Creando entorno virtual..."

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
else
    echo "El entorno .venv ya existe."
fi

echo "[4/6] Activando entorno virtual..."
source .venv/bin/activate

echo "[5/6] Actualizando pip..."
python -m pip install --upgrade pip setuptools wheel

echo "[6/6] Instalando librerías..."
pip install -r requirements.txt

echo ""
echo "======================================"
echo " Instalación completada"
echo "======================================"
echo ""
echo "Para ejecutar:"
echo "source .venv/bin/activate"
echo "python -m src.assistant_voice"