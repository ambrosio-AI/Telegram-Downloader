"""Backends: Telethon (cuenta real) y Demo (sin cuenta, para ver la interfaz)."""
from __future__ import annotations

import asyncio
import random
from pathlib import Path

VIDEO = {"mp4", "mkv", "avi", "mov", "webm", "m4v", "ts", "mpg", "mpeg", "flv", "wmv"}
AUDIO = {"mp3", "flac", "m4a", "aac", "ogg", "opus", "wav", "wma", "ape", "alac"}
IMAGE = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "heic"}
ARCHIVE = {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "img"}
DOC = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md", "csv", "epub", "mobi", "json", "xml"}


def classify(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    if ext in VIDEO:
        return "video"
    if ext in AUDIO:
        return "audio"
    if ext in IMAGE:
        return "imagen"
    if ext in ARCHIVE:
        return "archivo"
    if ext in DOC:
        return "documento"
    return "otro"


def _match(item: dict, *, q=None, exts=None, types=None, min_size=None, max_size=None, since=None, until=None) -> bool:
    if q and q.lower() not in item["file_name"].lower():
        return False
    if exts:
        want = {e.lower().lstrip(".") for e in exts}
        if Path(item["file_name"]).suffix.lower().lstrip(".") not in want:
            return False
    if types:
        if classify(item["file_name"]) not in {t.lower() for t in types}:
            return False
    if min_size and item["size"] < int(min_size):
        return False
    if max_size and item["size"] > int(max_size):
        return False
    if since and item["date"] < float(since):
        return False
    if until and item["date"] > float(until):
        return False
    return True


# --------------------------------------------------------------------------- #
#  Demo: no toca Telegram, no necesita sesión. Sirve para probar la interfaz.
# --------------------------------------------------------------------------- #
class DemoBackend:
    state = "demo"
    me = {"username": "demo", "name": "Modo demo (sin cuenta)"}

    def __init__(self):
        self._chats = [
            {"id": -1001000000001, "title": "Grupo Demo · Cursos", "kind": "grupo"},
            {"id": -1001000000002, "title": "Grupo Demo · Pelis y series", "kind": "canal"},
            {"id": 777000123, "title": "Mensajes guardados", "kind": "propio"},
        ]

    async def start(self):
        return self

    async def close(self):
        pass

    async def dialogs(self) -> list[dict]:
        return list(self._chats)

    def _all(self, chat_id: int) -> list[dict]:
        rnd = random.Random(int(chat_id))
        exts = ["mp4", "mkv", "zip", "pdf", "flac", "jpg", "iso", "docx", "rar", "epub", "mp3", "avi", "png"]
        out = []
        for i in range(1, 141):
            ext = exts[rnd.randrange(len(exts))]
            size = int(rnd.choice([0.2, 3, 18, 90, 450, 1400, 2600]) * 1024 * 1024) + rnd.randint(0, 900_000)
            out.append(
                {
                    "chat_id": int(chat_id),
                    "msg_id": 100000 - i,
                    "file_name": f"demo_{abs(int(chat_id)) % 1000}_{i:03d}.{ext}",
                    "size": size,
                    "date": 1781000000 - i * 7_200,
                    "sender": rnd.choice(["Ana", "Luis", "Marta", "Pepe", "bot_archivos"]),
                    "file_unique_id": f"demo-{chat_id}-{i}",
                    "kind": classify(f"x.{ext}"),
                }
            )
        return out

    async def list_files(self, chat_id: int, *, offset: int = 0, limit: int = 200, docs_only: bool = True, **filters) -> dict:
        await asyncio.sleep(0.05)
        allf = self._all(chat_id)
        # orden descendente por fecha, y "offset" = índice de continuación
        start = int(offset or 0)
        matched, idx = [], start
        while idx < len(allf) and len(matched) < limit:
            if _match(allf[idx], **filters):
                matched.append(allf[idx])
            idx += 1
        return {"items": matched, "next_offset": idx if idx < len(allf) else None, "scanned": idx - start}

    async def download(self, item: dict, dest_path: Path, progress=None, is_cancelled=None) -> None:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        total = min(int(item.get("size") or 1_000_000), 4 * 1024 * 1024)
        chunk = max(65_536, total // 20)
        done = 0
        with open(dest_path, "wb") as fh:
            while done < total:
                if is_cancelled and is_cancelled():
                    raise _Cancelled()
                block = min(chunk, total - done)
                fh.write(b"\0" * block)
                done += block
                if progress:
                    progress(done, total)
                await asyncio.sleep(0.08)


class _Cancelled(Exception):
    pass


# --------------------------------------------------------------------------- #
#  Telethon: cuenta real. Solo se importa aquí, así el modo demo no lo necesita.
# --------------------------------------------------------------------------- #
class TelethonBackend:
    def __init__(self, cfg: dict):
        from . import config as cfgmod  # import local para no arrastrar dependencias

        self.cfg = cfg
        self.api_id, self.api_hash = cfgmod.api_credentials(cfg)
        self.session = cfgmod.session_path(cfg)
        self.session_file = cfgmod.session_file(cfg)
        self.client = None
        self.me = None
        self.state = "no_session"
        self._sender_cache: dict[int, str] = {}
        self._titles: dict[int, str] = {}

    async def start(self):
        if not self.api_id or not self.api_hash:
            self.state = "no_credentials"
            return self
        if not self.session_file.exists():
            self.state = "no_session"
            return self
        try:
            from telethon import TelegramClient
        except ModuleNotFoundError:
            self.state = "no_telethon"
            return self
        try:
            self.client = TelegramClient(str(self.session), self.api_id, self.api_hash)
            await self.client.connect()
            if not await self.client.is_user_authorized():
                self.state = "no_session"
                return self
            me = await self.client.get_me()
            self.me = {"username": me.username or "", "name": " ".join(x for x in [me.first_name, me.last_name] if x)}
            self.state = "ok"
        except Exception as exc:  # credenciales inválidas, red, sesión corrupta…
            self.state = f"error: {exc.__class__.__name__}: {exc}"
        return self

    async def close(self):
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass

    async def dialogs(self) -> list[dict]:
        out = []
        async for d in self.client.iter_dialogs():
            kind = "canal" if (d.is_channel and not d.is_group) else ("grupo" if (d.is_group or d.is_channel) else "propio")
            if d.entity and getattr(d.entity, "bot", False):
                kind = "bot"
            out.append({"id": int(d.id), "title": d.title or "(sin nombre)", "kind": kind})
        out.sort(key=lambda x: x["title"].lower())
        return out

    async def _sender_name(self, msg) -> str:
        sid = getattr(msg, "sender_id", None)
        if sid is None:
            return ""
        if sid in self._sender_cache:
            return self._sender_cache[sid]
        name = ""
        try:
            ent = await msg.get_sender()
            if ent is not None:
                name = getattr(ent, "title", None) or " ".join(
                    x for x in [getattr(ent, "first_name", None), getattr(ent, "last_name", None)] if x
                )
        except Exception:
            name = ""
        name = name or str(sid)
        self._sender_cache[sid] = name
        return name

    async def list_files(self, chat_id: int, *, offset: int = 0, limit: int = 200, scan_cap: int = 20000,
                         docs_only: bool = True, **filters) -> dict:
        entity = await self.client.get_entity(int(chat_id))
        title = getattr(entity, "title", None) or str(chat_id)
        self._titles[int(chat_id)] = title
        kwargs = {"limit": int(scan_cap), "offset_id": int(offset or 0)}
        if docs_only:
            # Filtro en el servidor de Telegram: trae TODOS los ficheros y es mucho más rápido
            # que recorrer mensaje a mensaje. Las fotos enviadas como foto no son documentos:
            # para incluirlas hay que pedir el tipo «imagen» (entonces sí se recorre todo).
            from telethon.tl.types import InputMessagesFilterDocument

            kwargs["filter"] = InputMessagesFilterDocument()
        matched, scanned, last_id = [], 0, offset or 0
        async for msg in self.client.iter_messages(entity, **kwargs):
            scanned += 1
            last_id = msg.id
            f = getattr(msg, "file", None)
            if not f:
                continue
            if getattr(msg, "photo", None) and not f.name:
                name = f"foto_{msg.id}.jpg"
            else:
                name = f.name or f"fichero_{msg.id}"
            item = {
                "chat_id": int(chat_id),
                "chat_title": title,
                "msg_id": msg.id,
                "file_name": name,
                "size": int(f.size or 0),
                "date": msg.date.timestamp() if msg.date else 0,
                "sender": await self._sender_name(msg),
                "file_unique_id": str(getattr(f, "unique_id", "") or f"{chat_id}:{msg.id}"),
                "kind": classify(name),
            }
            if _match(item, **filters):
                matched.append(item)
                if len(matched) >= limit:
                    break
        return {
            "items": matched,
            "next_offset": last_id if scanned >= scan_cap or len(matched) >= limit else None,
            "scanned": scanned,
        }

    async def download(self, item: dict, dest_path: Path, progress=None, is_cancelled=None) -> None:
        entity = await self.client.get_entity(int(item["chat_id"]))
        msg = await self.client.get_messages(entity, ids=int(item["msg_id"]))
        if msg is None:
            raise RuntimeError("el mensaje ya no existe en el grupo")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest_path.with_name(dest_path.name + ".part")
        last = {"t": 0.0}

        def cb(received: int, total: int):
            if is_cancelled and is_cancelled():
                raise _Cancelled()
            if progress and (received == total):
                progress(received, total or received)
                return
            import time as _t

            now = _t.time()
            if now - last["t"] > 0.5:
                last["t"] = now
                if progress:
                    progress(received, total or received)

        try:
            await self.client.download_media(msg, file=str(tmp), progress_callback=cb)
            tmp.replace(dest_path)
        except BaseException:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise


def make_backend(cfg: dict, demo: bool = False):
    if demo:
        return DemoBackend()
    return TelethonBackend(cfg)
