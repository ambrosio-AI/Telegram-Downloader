#!/bin/bash
# Telegram-Downloader · enlaza tu cuenta de Telegram (solo hace falta la primera vez).
# Doble clic en este fichero desde el Finder.
cd "$(dirname "$0")" || exit 1
[ -t 1 ] && clear
echo "════════════════════════════════════════════════════════════"
echo "   Telegram-Downloader · ENLAZAR MI CUENTA (una sola vez)"
echo "════════════════════════════════════════════════════════════"
echo

if [ ! -f config.toml ]; then
  cp config.example.toml config.toml 2>/dev/null
  echo "  Falta un paso pequeño: hace falta tu api_id y tu api_hash."
  echo
  echo "  1) Entra en https://my.telegram.org con tu número de Telegram."
  echo "  2) Abre «API development tools», crea una aplicación (el nombre da igual)."
  echo "  3) Copia api_id y api_hash en el fichero config.toml que acabo de abrirte."
  echo "  4) Vuelve a hacer doble clic en este lanzador."
  echo
  open -e config.toml 2>/dev/null
  read -n 1 -s -r -p "  Pulsa una tecla para cerrar…"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "  Preparando el programa (solo esta vez, tarda un minuto)…"
  python3 -m venv .venv \
    && ./.venv/bin/python -m pip install -q --upgrade pip \
    && ./.venv/bin/python -m pip install -q -r requirements.txt
fi

echo "  Se va a mostrar un código QR en esta ventana."
echo "  En el móvil: Telegram → Ajustes → Dispositivos → Enlazar dispositivo."
echo
./.venv/bin/python -m telegram_downloader login --qr
echo
read -n 1 -s -r -p "  Pulsa una tecla para cerrar…"
