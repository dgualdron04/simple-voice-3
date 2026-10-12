#!/usr/bin/env bash

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [ ! -d ".venv" ]; then
    echo "No existe .venv. Ejecuta primero:"
    echo "./setup_linux.sh"
    exit 1
fi

source .venv/bin/activate

python -m src.assistant_voice