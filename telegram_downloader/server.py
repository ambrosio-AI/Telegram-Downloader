"""Interfaz web local servida con la biblioteca estándar (sin dependencias extra).

Un hilo ejecuta el bucle asíncrono (Telethon + la cola de descargas); el servidor
HTTP atiende la página y la API. Así funciona igual en Python 3.9 que en 3.14,
sin compiladores ni paquetes frágiles.
"""
from __future__ import annotations

import asyncio
import json
import os
import string as _string
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config as cfgmod
from .backends import make_backend
from .store import Store
from .worker import QueueWorker

STATIC = Path(__file__).resolve().parent.parent / "static"


class HttpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _fs_listing(path: str | None) -> dict:
    home = Path.home()
    if os.name == "nt":
        roots = [f"{c}:\\" for c in _string.ascii_uppercase if Path(f"{c}:\\").exists()]
    else:
        roots = ["/", str(home)]
    if not path:
        return {"path": "", "parent": None, "dirs": [str(home)], "roots": roots, "note": ""}
    p = Path(path).expanduser()
    note = ""
    if not p.is_dir():
        # La carpeta puede no existir todavía (es normal antes de la primera descarga):
        # en vez de fallar, abrimos la carpeta existente más cercana por encima.
        note = f"«{p}» todavía no existe; se ha abierto la carpeta superior"
        q = p
        while q != q.parent and not q.is_dir():
            q = q.parent
        if not q.is_dir():
            raise HttpError(400, f"no es una carpeta: {p}")
        p = q
    dirs = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            try:
                if child.is_dir() and not child.name.startswith("."):
                    dirs.append(str(child))
            except OSError:
                continue
    except PermissionError:
        pass
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": dirs,
            "roots": roots, "note": note}


class App:
    """Motor asíncrono (Telethon + cola) en un hilo, con la API compartida."""

    def __init__(self, cfg: dict, demo: bool = False):
        self.cfg = cfg
        self.demo = demo
        self.store = Store(cfgmod.data_dir(cfg) / "telegram-downloader.db")
        try:  # la carpeta por defecto puede no existir aún; así el explorador siempre abre en sitio real
            cfgmod.downloads_root(cfg).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.backend = None
        self.worker = None
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="telegram-downloader", daemon=True)

    # ------------------------------ ciclo de vida ------------------------------ #
    def start(self, timeout: float = 40.0) -> "App":
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("el motor no ha arrancado a tiempo")
        return self

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self) -> None:
        self.backend = make_backend(self.cfg, demo=self.demo)
        await self.backend.start()
        self.worker = QueueWorker(self.store, self.backend, self.cfg)
        task = asyncio.create_task(self.worker.run())
        self._ready.set()
        try:
            while not self._stop.is_set():
                await asyncio.sleep(0.3)
        finally:
            self.worker.stop.set()
            task.cancel()
            try:
                await task
            except BaseException:
                pass
            await self.backend.close()
            self.store.close()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def call(self, coro, timeout: float = 120.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    # -------------------------------- endpoints -------------------------------- #
    def _need_session(self) -> None:
        if self.backend.state not in ("ok", "demo"):
            raise HttpError(409, f"sin sesión de Telegram ({self.backend.state})")

    def status(self) -> dict:
        return {
            "state": self.backend.state,
            "me": self.backend.me,
            "demo": bool(self.demo),
            "counts": self.store.counts(),
            "paused": self.worker.paused,
            "current": self.worker.current,
            "dest_root": str(cfgmod.downloads_root(self.cfg)),
            "subfolder_por_grupo": bool(self.cfg["downloads"].get("subfolder_por_grupo", True)),
            "session_file": str(cfgmod.session_file(self.cfg)),
        }

    def handle(self, path: str, method: str, query: dict, body: dict | None):
        if path in ("/", "/index.html"):
            return 200, (STATIC / "index.html").read_bytes()

        if path == "/api/status":
            return 200, self.status()

        if path == "/api/dialogs":
            self._need_session()
            items = self.call(self.backend.dialogs())
            q = (query.get("q") or "").lower()
            if q:
                items = [d for d in items if q in d["title"].lower()]
            return 200, {"items": items}

        if path == "/api/list":
            self._need_session()
            body = body or {}
            if not body.get("chat_id"):
                raise HttpError(400, "falta chat_id")
            filters = {
                "q": body.get("q") or None,
                "exts": body.get("exts") or None,
                "types": body.get("types") or None,
                "min_size": body.get("min_size") or None,
                "max_size": body.get("max_size") or None,
                "since": body.get("since") or None,
                "until": body.get("until") or None,
            }
            types = [str(t).lower() for t in (body.get("types") or [])]
            exts = [str(e).lower().lstrip(".") for e in (body.get("exts") or [])]
            img_exts = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "heic"}
            # Con documentos, Telegram filtra en su servidor: trae TODOS los ficheros y va mucho
            # más rápido. Si se piden imágenes (que en Telegram no son documentos), se recorre todo.
            docs_only = not (("imagen" in types or "image" in types) or bool(set(exts) & img_exts))
            try:
                res = self.call(
                    self.backend.list_files(
                        int(body["chat_id"]),
                        offset=int(body.get("offset") or 0),
                        limit=min(int(body.get("limit") or 200), 500),
                        docs_only=docs_only,
                        **filters,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                raise HttpError(500, f"{exc.__class__.__name__}: {exc}") from exc
            for it in res["items"]:
                it["downloaded"] = self.store.is_downloaded(it.get("file_unique_id", ""))
                it.setdefault("chat_title", "")
            return 200, res

        if path == "/api/queue" and method == "GET":
            return 200, {
                "items": self.store.queue(),
                "counts": self.store.counts(),
                "paused": self.worker.paused,
                "current": self.worker.current,
            }

        if path == "/api/queue" and method == "POST":
            body = body or {}
            items = body.get("items") or []
            if not items:
                raise HttpError(400, "no has seleccionado ningún fichero")
            dest = Path(body.get("dest_dir") or cfgmod.downloads_root(self.cfg)).expanduser()
            try:
                dest.mkdir(parents=True, exist_ok=True)
                probe = dest / ".td-prueba"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
            except Exception as exc:  # noqa: BLE001
                raise HttpError(400, f"no puedo escribir en {dest}: {exc}") from exc
            return 200, self.store.enqueue(
                items, str(dest),
                skip_existing=bool(body.get("skip_existing", self.cfg["downloads"].get("skip_existing", True))),
            )

        if path == "/api/queue/action":
            act = ((body or {}).get("action") or "").strip()
            qid = (body or {}).get("id")
            return 200, {"message": self.worker.control(act, int(qid) if qid else None)}

        if path == "/api/manifest":
            return 200, {"items": self.store.manifest(query.get("q"))}

        if path == "/api/fs":
            return 200, _fs_listing(query.get("path"))

        if path == "/api/pick-folder":
            return 200, self.pick_folder_native()

        raise HttpError(404, f"ruta desconocida: {path}")

    # ---------------------- selector de carpetas del sistema ---------------------- #
    def pick_folder_native(self) -> dict:
        """En macOS abre el diálogo nativo de carpetas (Finder). En otros sistemas, no."""
        import platform
        import subprocess

        if platform.system() != "Darwin":
            return {"ok": False, "path": None, "reason": "este sistema no tiene selector nativo"}
        script = 'tell application "System Events" to POSIX path of (choose folder with prompt "Elige la carpeta de descargas")'
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=300)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "path": None, "reason": f"{exc.__class__.__name__}"}
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out:
            return {"ok": True, "path": out.rstrip("/"), "reason": ""}
        err = (r.stderr or "").strip()
        if "User canceled" in err or "(-128)" in err:
            return {"ok": True, "path": None, "reason": "cancelado"}
        return {"ok": False, "path": None, "reason": err[:160] or "no se pudo abrir el diálogo"}


class Handler(BaseHTTPRequestHandler):
    server_version = "Telegram-Downloader"
    app: App = None

    def log_message(self, *args):  # sin ruido en consola
        pass

    def _send(self, code: int, payload, ctype: str = "application/json; charset=utf-8") -> None:
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        u = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = None
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if raw:
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    self._send(400, {"detail": "cuerpo JSON inválido"})
                    return
        try:
            code, payload = self.app.handle(u.path, method, query, body)
        except HttpError as exc:
            code, payload = exc.code, {"detail": exc.message}
        except Exception as exc:  # noqa: BLE001
            code, payload = 500, {"detail": f"{exc.__class__.__name__}: {exc}"}
        if isinstance(payload, bytes):
            self._send(code, payload, "text/html; charset=utf-8")
        else:
            self._send(code, json.dumps(payload, ensure_ascii=False))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(config_path: str | None = None, demo: bool = False, host: str | None = None,
          port: int | None = None, open_browser: bool = True) -> None:
    cfg = cfgmod.load(config_path)
    h = host or cfg["server"]["host"]
    p = int(port or cfg["server"]["port"])

    app = App(cfg, demo=demo).start()
    Handler.app = app
    try:
        httpd = Server((h, p), Handler)
    except OSError as exc:
        app.stop()
        print(f"\n  No he podido abrir el puerto {p}: {exc}")
        print(f"  Prueba con otro puerto:  --port {p + 1}\n")
        return
    url = f"http://{'127.0.0.1' if h in ('0.0.0.0', '') else h}:{p}/"
    print(f"\n  Telegram-Downloader · interfaz en {url}", flush=True)
    print("  Deja esta ventana abierta mientras descargas. Ctrl+C para parar.\n", flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  parando…")
    finally:
        httpd.shutdown()
        httpd.server_close()
        app.stop()
