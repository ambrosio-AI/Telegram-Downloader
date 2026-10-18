@echo off
rem Telegram-Downloader · arranque rápido (Windows)
rem   run.bat            -> abre la interfaz
rem   run.bat demo       -> interfaz con datos de prueba (sin cuenta de Telegram)
rem   run.bat login --qr -> crea la sesión de tu cuenta
cd /d "%~dp0"

if not exist .venv (
  echo [i] creando entorno virtual .venv ...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\python -m pip install -q -r requirements.txt
)

if "%~1"=="demo" (
  .venv\Scripts\python -m telegram_downloader serve --demo
  goto :eof
)
.venv\Scripts\python -m telegram_downloader %*
