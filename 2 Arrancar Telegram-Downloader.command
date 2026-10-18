#!/bin/bash
# Telegram-Downloader · arranca la interfaz. Doble clic en este fichero desde el Finder.
cd "$(dirname "$0")" || exit 1
[ -t 1 ] && clear
echo "════════════════════════════════════════════════════════════"
echo "   Telegram-Downloader"
echo
echo "   Se abrirá sola una pestaña en tu navegador."
echo "   Deja ESTA ventana abierta mientras descargas."
echo "   Para parar: cierra esta ventana o pulsa Ctrl+C."
echo "════════════════════════════════════════════════════════════"
echo

if [ ! -d .venv ]; then
  echo "  Preparando el programa (solo esta vez, tarda un minuto)…"
  python3 -m venv .venv \
    && ./.venv/bin/python -m pip install -q --upgrade pip \
    && ./.venv/bin/python -m pip install -q -r requirements.txt
fi

if [ ! -f data/tg.session ]; then
  echo "  Todavía no has enlazado tu cuenta."
  echo "  Cierra esto y haz doble clic primero en:"
  echo
  echo "      «1 Enlazar con Telegram.command»"
  echo
  read -n 1 -s -r -p "  Pulsa una tecla para salir…"
  exit 1
fi

exec ./.venv/bin/python -m telegram_downloader serve
