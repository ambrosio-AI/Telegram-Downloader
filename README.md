# Telegram-Downloader

Descargador por cola para los **archivos de un grupo de Telegram**, usando **tu propia cuenta**.
Lista lo que hay en el grupo, tú marcas lo que quieres, se encola y se descarga en la carpeta que
elijas. Funciona en tu PC: **no necesita nada de nadie más**.

## Qué hace

- Lista los ficheros (vídeo, audio, imagen, documento, comprimidos/ISO) de **cualquier grupo o chat**
  donde esté tu cuenta, con filtros por texto, extensión, tipo, tamaño y fechas. **Trae la lista
  completa de un tirón** (no una tanda): los ficheros se muestran todos por defecto, mientras abajo
  te va contando cuántos lleva y cómo va. Si el grupo tiene una historia enorme, puedes parar con el
  botón *Parar* y afinar con los filtros.
- Marca cuáles quieres y los **encola** con la carpeta destino que elijas (o varias, según te dé).
- Descarga **de uno en uno**, con barra de progreso, pausa, cancelar, reintentar y reanudar la cola.
- Lleva un **registro** de lo ya descargado: si vuelves a encolar lo mismo, lo salta.
- Puede funcionar sin interfaz: `list`, `add`, `run` (útil para dejarlo en un cron).

## Qué NO hace

- No sube nada. No manda mensajes. No publica. Solo **lee** el grupo que tú elijas y **descarga**.
- No habla con ningún servidor mío ni de terceros: únicamente con Telegram.
- No borra nada del grupo.

## Requisitos

- **Python 3.9 o superior** en ese PC. Nada más: ni Docker, ni compiladores, ni cuentas de terceros.
- Conexión a internet.
- Tu cuenta de Telegram (la sesión se crea en ese mismo PC).

Probado de verdad en: **macOS** (Python 3.9 del sistema) y **Linux** (Python 3.14). En Windows no lo he
ejecutado aún: el lanzador está hecho, pero si algo falla se corrige en un momento.

## Instalación (Linux / macOS)

```bash
unzip Telegram-Downloader.zip && cd Telegram-Downloader
./run.sh demo            # opcional: mira la interfaz con datos falsos, sin usar tu cuenta
```

## Instalación (Windows)

```bat
:: descomprime el zip y abre una consola en esa carpeta
run.bat demo
```

## Puesta en marcha (5 minutos, una sola vez)

1. **Saca tus claves de API**: entra en <https://my.telegram.org> con tu número, ve a
   *API development tools*, crea una aplicación cualquiera y copia **api_id** y **api_hash**.
2. Copia `config.example.toml` a `config.toml` y pega ahí los dos valores.
   (También vale por variables de entorno: `TD_API_ID`, `TD_API_HASH`.)
3. Crea la sesión:

   ```bash
   ./run.sh login --qr      # te muestra un QR: móvil -> Ajustes -> Dispositivos -> Enlazar dispositivo
   # o bien:
   ./run.sh login           # te pide teléfono, código y, si la tienes, la contraseña de 2FA
   ```

   Todo se teclea **en tu ordenador**. La sesión se guarda en `data/tg.session`.
4. Arranca la interfaz:

   ```bash
   ./run.sh                 # se abre en http://127.0.0.1:8765
   ```
5. En la interfaz: *Cargar chats* → elige el grupo → filtra → marca ficheros → elige carpeta
   destino → *Añadir a la cola*. La pestaña **Cola** va mostrando el progreso.

## En el Mac (doble clic, sin consola)

En la carpeta del programa tienes dos lanzadores:

1. **`1 Enlazar con Telegram.command`** — solo la primera vez. Te abre el `config.toml` para poner tus
   claves y después te muestra el **QR** para enlazar la cuenta desde el móvil.
2. **`2 Arrancar Telegram-Downloader.command`** — abre la interfaz en el navegador. Deja esa ventana
   abierta mientras descargas; para parar, ciérrala o pulsa Ctrl+C.

Si macOS te avisa de que el fichero «no se puede abrir porque proviene de un desarrollador no
identificado», haz clic derecho → *Abrir* una sola vez (no hace falta tocar nada más).

## Por consola (sin interfaz)

```bash
./run.sh list --chat -1001234567890 --min-mb 100 --ext mp4,mkv
./run.sh add  --chat -1001234567890 --dest /mnt/disco/pelis --min-mb 100 --ext mp4 --run
./run.sh run --follow          # procesa la cola y se queda vigilando
./run.sh status
```

El `--chat` es el id que aparece al usar `list`/la interfaz (grupo/supergrupo empieza por `-100`).

## Seguridad

- `data/tg.session` **es tu cuenta**: quien tenga ese fichero puede usarla como tú. No lo copies ni
  lo subas a ningún sitio. El programa lo crea con permisos de solo-lectura para tu usuario.
- Para cortar el acceso en cualquier momento: **Telegram → Ajustes → Dispositivos → terminar sesión**
  (verás el dispositivo como `Telegram-Downloader` / Python).
- La interfaz escucha **solo en 127.0.0.1** (tu propia máquina). No la abras a la red.
- `api_id`/`api_hash` y la sesión se quedan en ese PC; no se envían a ninguna parte salvo a Telegram.

## Límites de Telegram (no son del programa)

- Archivos de hasta **2 GB** por fichero (**4 GB** si tu cuenta tiene Premium).
- Si encolas muchos seguidos, Telegram puede pedir una **espera temporal** (*FloodWait*): el programa
  espera y reintenta solo.
- Los **mensajes guardados** y algunos chats privados no dejan listar ficheros antiguos igual que un
  grupo: es limitación de Telegram, no un fallo.

## Problemas típicos

- **«sin sesión (no_session)»**: falta el paso 3 (`login`). Repítelo.
- **«API_ID_INVALID»** al hacer login: api_id/api_hash mal copiados en `config.toml`.
- **No aparecen mensajes antiguos en un grupo grande**: sube el tope con `--limit` en consola, o pulsa
  *Cargar más* en la interfaz.
- **El fichero se queda en `.part`**: es una descarga cortada; pulsa *reintentar* y se rehace.
- **Móvil con 2FA**: el login pedirá tu contraseña de dos pasos. Se teclea en tu terminal, no se guarda.

## Estructura

```
Telegram-Downloader/
  run.sh / run.bat      arranque rápido
  config.example.toml   plantilla de configuración (cópiala a config.toml)
  telegram_downloader/              código
    cli.py              comandos (login, serve, list, add, run, status, selftest)
    server.py           interfaz web local
    backends.py         Telethon (cuenta real) + modo demo
    worker.py           cola de descargas
    store.py            base de datos local (cola + registro)
  static/index.html     interfaz
  data/                 sesión, base de datos y registro (no compartir)
```

Comprobar que todo funciona sin usar tu cuenta:

```bash
./run.sh selftest
```
