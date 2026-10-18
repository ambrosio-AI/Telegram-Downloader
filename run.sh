#!/usr/bin/env bash
# Telegram-Downloader · arranque rápido (Linux / macOS)
#   ./run.sh            -> abre la interfaz
#   ./run.sh demo       -> interfaz con datos de prueba (sin cuenta de Telegram)
#   ./run.sh login --qr -> crea la sesión de tu cuenta
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "[i] creando entorno virtual (.venv)…"
  python3 -m venv .venv
  ./.venv/bin/python -m pip install -q --upgrade pip
  ./.venv/bin/python -m pip install -q -r requirements.txt
fi

if [ "${1:-}" = "demo" ]; then
  exec ./.venv/bin/python -m telegram_downloader serve --demo
fi
exec ./.venv/bin/python -m telegram_downloader "$@"
