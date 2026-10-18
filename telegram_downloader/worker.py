"""Cola de descargas: un fichero a la vez, con progreso, cancelación y reintentos."""
from __future__ import annotations

import asyncio
import re
import unicodedata
from pathlib import Path

from . import config as cfgmod

SAFE = re.compile(r"[^\w\.\-\+\(\)\[\] ]+", re.UNICODE)


def safe_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFC", name)
    name = SAFE.sub("_", name).strip(" .")
    return name or "fichero"


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
            base = base / safe_name(item["chat_title"])
        return base

    def _target(self, item: dict) -> Path:
        d = self._dest_dir(item)
        name = safe_name(item["file_name"])
        p = d / name
        if p.exists():
            stem, dot, ext = name.rpartition(".")
            p = d / (f"{stem}-{item['msg_id']}.{ext}" if dot else f"{name}-{item['msg_id']}")
        return p

    async def run(self) -> None:
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

        def progress(received: int, total: int) -> None:
            self.store.set_progress(qid, received)

        def is_cancelled() -> bool:
            return self.store.status_of(qid) == "cancelled"

        try:
            await self.backend.download(item, target, progress=progress, is_cancelled=is_cancelled)
            size = target.stat().st_size if target.exists() else (item.get("size") or 0)
            self.store.set_progress(qid, size)
            self.store.record_download(item, str(target), size)
            self.store.finish(qid, "done")
        except Cancelled:
            self.store.finish(qid, "cancelled", "cancelado")
        except BaseException as exc:  # noqa: BLE001 — se guarda el motivo real
            name = exc.__class__.__name__
            if is_cancelled():
                self.store.finish(qid, "cancelled", "cancelado")
            elif name in {"FloodWaitError", "TimeoutError", "ConnectionError", "OSError"} and tries < max_tries:
                self.store.requeue(qid)
            else:
                self.store.finish(qid, "error", f"{name}: {exc}")
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
