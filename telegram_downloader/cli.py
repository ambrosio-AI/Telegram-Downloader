"""Línea de órdenes: login / serve / list / add / run / status / selftest."""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from . import __version__, config as cfgmod
from .backends import make_backend
from .store import Store
from .worker import QueueWorker, human


# --------------------------------------------------------------------------- #
def _print_files(items: list[dict]) -> None:
    if not items:
        print("  (sin resultados)")
        return
    print(f"  {'msg_id':>9}  {'tamaño':>10}  {'fecha':<17}  nombre")
    for it in items:
        fecha = time.strftime("%Y-%m-%d %H:%M", time.localtime(it.get("date") or 0))
        print(f"  {it['msg_id']:>9}  {human(int(it.get('size') or 0)):>10}  {fecha:<17}  {it['file_name']}")


async def _run_queue(store: Store, backend, cfg: dict, follow: bool = False) -> None:
    worker = QueueWorker(store, backend, cfg)
    task = asyncio.create_task(worker.run())
    print("  cola en marcha (Ctrl+C para parar)…")
    try:
        idle = 0
        while True:
            await asyncio.sleep(1.0)
            c = store.counts()
            print(f"\r  pendientes={c['pending']} en curso={c['running']} hechos={c['done']} errores={c['error']}   ", end="")
            if c["pending"] == 0 and c["running"] == 0:
                idle += 1
                if idle >= 2 and not follow:
                    break
            else:
                idle = 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        worker.stop.set()
        task.cancel()
    print("\n  cola terminada.")


async def _login(cfg: dict, args) -> int:
    try:
        from telethon import TelegramClient
    except ModuleNotFoundError:
        print("  Falta la librería telethon: instala con `pip install -r requirements.txt`.")
        return 2
    api_id, api_hash = cfgmod.api_credentials(cfg)
    if args.api_id:
        api_id = int(args.api_id)
    if args.api_hash:
        api_hash = args.api_hash
    if not api_id or not api_hash:
        print("  Faltan api_id / api_hash. Sácalos en https://my.telegram.org -> API development tools")
        print("  y ponlos en config.toml (o pásalos con --api-id/--api-hash).")
        return 2

    session = cfgmod.session_path(cfg)
    client = TelegramClient(str(session), api_id, api_hash)
    if args.qr:
        await client.connect()
        print("\n  Abre Telegram en el móvil: Ajustes -> Dispositivos -> Enlazar dispositivo")
        qr = await client.qr_login()
        try:
            import qrcode

            q = qrcode.QRCode(border=1)
            q.add_data(qr.url)
            q.make()
            q.print_ascii(invert=True)
        except Exception:
            pass
        print(f"  Enlace directo: {qr.url}\n")
        while True:
            try:
                await qr.wait(timeout=25)
                break
            except asyncio.TimeoutError:
                await qr.recreate()
                print(f"  (QR renovado) {qr.url}")
            except Exception as exc:  # noqa: BLE001
                print(f"  error en el login por QR: {exc}")
                return 1
    else:
        print("\n  Introduce el teléfono, el código que te llegue y, si tienes, tu contraseña de 2FA.")
        print("  Todo se teclea AQUÍ, en tu ordenador. Aquí se guarda la sesión y no sale de esta máquina.\n")
        await client.start(phone=args.phone)

    me = await client.get_me()
    print(f"\n  Sesión lista para @{me.username or me.id} ({me.first_name or ''})")
    print(f"  Fichero de sesión: {cfgmod.session_file(cfg)}")
    print("  AVISO: ese fichero da acceso completo a tu cuenta. No lo copies ni lo compartas.")
    print("  Para cortarlo: Telegram -> Ajustes -> Dispositivos -> terminar sesión.")
    await client.disconnect()
    return 0


async def _selftest(tmp: Path) -> int:
    cfg = cfgmod.load(None)
    cfg["downloads"]["root"] = str(tmp / "descargas")
    cfg["_data_dir"] = str(tmp / "data")
    store = Store(tmp / "data" / "telegram-downloader.db")
    backend = make_backend(cfg, demo=True)
    await backend.start()
    chat = (await backend.dialogs())[0]
    found = await backend.list_files(chat["id"], limit=5, min_size=5 * 1024 * 1024)
    print(f"  1) lista: {len(found['items'])} ficheros de {chat['title']} (siguiente offset: {found['next_offset']})")
    res = store.enqueue(found["items"], str(tmp / "descargas"))
    print(f"  2) cola: añadidos={res['added']} ya_en_cola={res['queued_before']} ya_descargados={res['skipped_done']}")
    again = store.enqueue(found["items"], str(tmp / "descargas"))
    print(f"  3) duplicados rechazados: {again['added']} añadidos, {again['queued_before']} ya en cola")
    worker = QueueWorker(store, backend, cfg)
    task = asyncio.create_task(worker.run())
    deadline = time.time() + 90
    while time.time() < deadline:
        c = store.counts()
        if c["pending"] == 0 and c["running"] == 0:
            break
        await asyncio.sleep(0.3)
    worker.stop.set()
    task.cancel()
    try:
        await task
    except BaseException:
        pass
    c = store.counts()
    files = sorted(p for p in (tmp / "descargas").rglob("*") if p.is_file())
    print(f"  4) cola: {c}")
    for f in files:
        print(f"     -> {f.relative_to(tmp)} ({human(f.stat().st_size)})")
    print(f"  5) registro de descargas: {len(store.manifest())} entrada(s)")
    await backend.close()
    store.close()
    ok = c["done"] == res["added"] and c["error"] == 0 and len(files) == res["added"]
    print("  RESULTADO:", "OK" if ok else "FALLO")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="telegram_downloader", description="Lista, selecciona y descarga archivos de un grupo de Telegram.")
    ap.add_argument("--config", default=None, help="ruta a config.toml (por defecto: ./config.toml)")
    ap.add_argument("--version", action="version", version=f"Telegram-Downloader {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="abre la interfaz en el navegador")
    p.add_argument("--demo", action="store_true", help="modo demo: sin cuenta de Telegram, datos falsos")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--no-browser", action="store_true")

    p = sub.add_parser("login", help="crea la sesión de tu cuenta (una sola vez)")
    p.add_argument("--qr", action="store_true", help="enlazar escaneando un QR con el móvil")
    p.add_argument("--phone", default=None)
    p.add_argument("--api-id", type=int, default=None)
    p.add_argument("--api-hash", default=None)

    def add_filters(sp):
        sp.add_argument("--q", default=None, help="texto en el nombre del fichero")
        sp.add_argument("--ext", default=None, help="extensiones separadas por comas, p.ej. mp4,mkv")
        sp.add_argument("--type", default=None, help="video,audio,imagen,archivo,documento,otro")
        sp.add_argument("--min-mb", type=float, default=0)
        sp.add_argument("--max-mb", type=float, default=0)
        sp.add_argument("--since", default=None, help="AAAA-MM-DD")
        sp.add_argument("--until", default=None, help="AAAA-MM-DD")

    p = sub.add_parser("list", help="lista los ficheros de un grupo")
    p.add_argument("--chat", type=int, required=True)
    p.add_argument("--limit", type=int, default=200)
    add_filters(p)

    p = sub.add_parser("add", help="encola los ficheros que casen con el filtro")
    p.add_argument("--chat", type=int, required=True)
    p.add_argument("--dest", required=True, help="carpeta destino")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--run", action="store_true", help="descargar ahora mismo en vez de solo encolar")
    add_filters(p)

    p = sub.add_parser("run", help="procesa la cola")
    p.add_argument("--follow", action="store_true", help="no salir cuando la cola quede vacía")

    sub.add_parser("status", help="estado de la cola")

    p = sub.add_parser("selftest", help="prueba interna en modo demo (no usa tu cuenta)")
    p.add_argument("--dir", default=None)

    args = ap.parse_args(argv)
    cfg = cfgmod.load(args.config)

    if args.cmd == "selftest":
        import tempfile

        tmp = Path(args.dir).expanduser() if args.dir else Path(tempfile.mkdtemp(prefix="td-selftest-"))
        tmp.mkdir(parents=True, exist_ok=True)
        print(f"  prueba en {tmp}")
        return asyncio.run(_selftest(tmp))

    if args.cmd == "login":
        return asyncio.run(_login(cfg, args))

    if args.cmd == "serve":
        from .server import serve

        serve(args.config, demo=args.demo, host=args.host, port=args.port, open_browser=not args.no_browser)
        return 0

    # ---- a partir de aquí hace falta base de datos y backend ----
    store = Store(cfgmod.data_dir(cfg) / "telegram-downloader.db")

    if args.cmd == "status":
        c = store.counts()
        print(f"  cola: pendientes={c['pending']} en curso={c['running']} hechos={c['done']} "
              f"errores={c['error']} cancelados={c['cancelled']} total={c['total']}")
        for it in store.queue(limit=15):
            print(f"  #{it['id']:<4} [{it['status']:<9}] {it['file_name'][:60]}")
        print(f"  descargados registrados: {len(store.manifest())}")
        return 0

    filters = {
        "q": args.q,
        "exts": [e for e in (args.ext or "").split(",") if e],
        "types": [t for t in (args.type or "").split(",") if t],
        "min_size": int((args.min_mb or 0) * 1024 * 1024),
        "max_size": int((args.max_mb or 0) * 1024 * 1024) if args.max_mb else None,
        "since": None,
        "until": None,
    }
    for key, val in (("since", args.since), ("until", args.until)):
        if val:
            try:
                filters[key] = time.mktime(time.strptime(val, "%Y-%m-%d"))
            except ValueError:
                print(f"  fecha inválida en --{key}: {val} (usa AAAA-MM-DD)")
                return 2

    async def _with_backend(coro_factory):
        backend = make_backend(cfg, demo=False)
        await backend.start()
        if backend.state != "ok":
            print(f"  No hay sesión utilizable ({backend.state}).")
            if backend.state == "no_session":
                print("  Ejecuta primero:  python -m telegram_downloader login      (o `--qr`)")
            return 2
        try:
            return await coro_factory(backend)
        finally:
            await backend.close()

    if args.cmd == "list":
        async def go(backend):
            res = await backend.list_files(args.chat, offset=0, limit=args.limit, **filters)
            print(f"  {len(res['items'])} resultado(s) (mensajes leídos: {res['scanned']}, siguiente offset: {res['next_offset']})")
            _print_files(res["items"])
            return 0

        return asyncio.run(_with_backend(go))

    if args.cmd == "add":
        async def go(backend):
            res = await backend.list_files(args.chat, offset=0, limit=args.limit, **filters)
            if not res["items"]:
                print("  nada que encolar con esos filtros")
                return 0
            dest = Path(args.dest).expanduser()
            dest.mkdir(parents=True, exist_ok=True)
            r = store.enqueue(res["items"], str(dest))
            print(f"  encolados={r['added']} ya_en_cola={r['queued_before']} ya_descargados={r['skipped_done']} -> {dest}")
            if args.run and r["added"]:
                await _run_queue(store, backend, cfg, follow=False)
            return 0

        return asyncio.run(_with_backend(go))

    if args.cmd == "run":
        async def go(backend):
            await _run_queue(store, backend, cfg, follow=args.follow)
            return 0

        return asyncio.run(_with_backend(go))

    print("  comando no reconocido")
    return 2


if __name__ == "__main__":
    sys.exit(main())
