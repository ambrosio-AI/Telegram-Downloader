"""Cola de descargas: un fichero a la vez, con progreso, cancelación y reintentos."""
from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from pathlib import Path

from . import config as cfgmod

SAFE = re.compile(r"[^\w\.\-\+\(\)\[\] ]+", re.UNICODE)


def safe_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFC", name)
    name = SAFE.sub("_", name).strip(" .")
    return name or "fichero"


def group_folder(title: str | None, chat_id) -> str:
    """Carpeta para un grupo. Si su nombre no deja nada legible (p. ej. solo emoticonos),
    se usa el id, para no acabar con carpetas llamadas «_»."""
    name = safe_name(title or "")
    if not re.search(r"[A-Za-z0-9]", name):
        return f"grupo-{str(chat_id).lstrip('-')}"
    return name


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


class Cancelled(Exception):
    pass


class QueueWorker:
    def __init__(self, store, backend, cfg: dict):
        self.store = store
        self.backend = backend
        self.cfg = cfg
        self.paused = False
        self.current: dict | None = None
        self.stop = asyncio.Event()

    def _dest_dir(self, item: dict) -> Path:
        base = Path(item["dest_dir"]).expanduser()
        if not base.is_absolute():
            base = cfgmod.BASE / base
        if self.cfg["downloads"].get("subfolder_por_grupo") and item.get("chat_title"):
            base = base / group_folder(item.get("chat_title"), item.get("chat_id"))
        return base

    def _target(self, item: dict) -> Path:
        d = self._dest_dir(item)
        name = safe_name(item["file_name"])
        known = self.store.known_path(item.get("file_unique_id") or "")
        if known and Path(known).parent == d:
            return Path(known)  # volver a descargar lo mismo en su carpeta: se reemplaza, no se duplica
        p = d / name
        if p.exists() and not (known and Path(known) == p):
            stem, dot, ext = name.rpartition(".")
            p = d / (f"{stem}-{item['msg_id']}.{ext}" if dot else f"{name}-{item['msg_id']}")
        return p

    async def run(self) -> None:
        reanudadas = self.store.reset_running()
        if reanudadas:
            print(f"  {reanudadas} descarga(s) que se quedaron a medias vuelven a la cola.", flush=True)
        while not self.stop.is_set():
            try:
                if self.paused:
                    await asyncio.sleep(0.6)
                    continue
                item = self.store.next_pending()
                if not item:
                    await asyncio.sleep(0.8)
                    continue
                if self.store.status_of(item["id"]) != "pending":
                    continue
                await self._process(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(1.0)

    async def _process(self, item: dict) -> None:
        qid = item["id"]
        self.current = {"id": qid, "file_name": item["file_name"]}
        self.store.mark_running(qid)
        target = self._target(item)
        tries = self.store.pending_tries(qid)
        max_tries = int(self.cfg["downloads"].get("max_retries") or 3)

        t0 = time.time()
        vel = {"t": None, "b": 0, "max": 0.0}

        def progress(received: int, total: int) -> None:
            self.store.set_progress(qid, received)
            ahora = time.time()
            if vel["t"] is None:                      # primera muestra: solo fija la referencia
                vel["t"], vel["b"] = ahora, received
                return
            dt = ahora - vel["t"]
            if dt >= 0.4:                            # ventana mínima para que la medida tenga sentido
                inst = (received - vel["b"]) / dt / 1048576.0
                if inst > vel["max"]:
                    vel["max"] = inst
                vel["t"], vel["b"] = ahora, received  # solo se mueve la referencia al medir

        def is_cancelled() -> bool:
            return self.store.status_of(qid) == "cancelled"

        try:
            await self.backend.download(item, target, progress=progress, is_cancelled=is_cancelled)
            segundos = time.time() - t0
            size = target.stat().st_size if target.exists() else (item.get("size") or 0)
            media = (size / segundos / 1048576.0) if segundos > 0.05 else None
            pico = vel["max"] or None
            self.store.set_progress(qid, size)
            self.store.record_download(item, str(target), size, duration=segundos,
                                       speed_avg=media, speed_max=pico)
            self.store.finish(qid, "done", duration=segundos, speed_avg=media, speed_max=pico)
        except Cancelled:
            self.store.finish(qid, "cancelled", "cancelado", duration=time.time() - t0)
        except BaseException as exc:  # noqa: BLE001 — se guarda el motivo real
            segundos = time.time() - t0
            name = exc.__class__.__name__
            if is_cancelled():
                self.store.finish(qid, "cancelled", "cancelado", duration=segundos)
            elif name in {"FloodWaitError", "TimeoutError", "ConnectionError", "OSError"} and tries < max_tries:
                self.store.requeue(qid)
            else:
                self.store.finish(qid, "error", f"{name}: {exc}", duration=segundos)
        finally:
            self.current = None

    def control(self, action: str, qid: int | None = None) -> str:
        if action == "pause":
            self.paused = True
            return "cola en pausa"
        if action == "resume":
            self.paused = False
            return "cola en marcha"
        if action == "cancel" and qid:
            self.store.cancel(qid)
            return f"cancelado #{qid}"
        if action == "retry" and qid:
            self.store.requeue(qid)
            return f"reintentando #{qid}"
        if action == "remove" and qid:
            self.store.remove(qid)
            return f"quitado de la cola #{qid}"
        if action == "retry_failed":
            n = self.store.retry_failed()
            return f"reintentando {n} fichero(s)"
        if action == "clear_done":
            n = self.store.clear_finished()
            return f"limpiada la cola: {n} fila(s)"
        return "accion desconocida"
