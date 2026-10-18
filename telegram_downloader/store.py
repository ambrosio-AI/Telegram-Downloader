"""Persistencia local (SQLite): cola de descargas + registro de lo ya descargado."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
create table if not exists queue (
  id           integer primary key autoincrement,
  chat_id      integer not null,
  chat_title   text,
  msg_id       integer not null,
  file_unique_id text,
  file_name    text not null,
  ext          text,
  size         integer default 0,
  dest_dir     text not null,
  status       text not null default 'pending',   -- pending|running|done|error|cancelled|skipped
  progress     integer default 0,
  error        text,
  tries        integer default 0,
  created_at   real,
  started_at   real,
  finished_at  real,
  unique(chat_id, msg_id)
);
create index if not exists idx_queue_status on queue(status);

create table if not exists manifest (
  file_unique_id text primary key,
  chat_id      integer,
  chat_title   text,
  msg_id       integer,
  file_name    text,
  size         integer,
  path         text,
  downloaded_at real
);
create index if not exists idx_manifest_name on manifest(file_name);
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    # ---------- utilidades ----------
    def _exec(self, sql: str, args: tuple = ()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self._db.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def _clean(item: dict) -> dict:
        return {
            "chat_id": int(item["chat_id"]),
            "chat_title": item.get("chat_title") or "",
            "msg_id": int(item["msg_id"]),
            "file_unique_id": item.get("file_unique_id") or "",
            "file_name": item["file_name"],
            "ext": (item.get("ext") or "").lstrip(".").lower(),
            "size": int(item.get("size") or 0),
        }

    # ---------- cola ----------
    def enqueue(self, items: list[dict], dest_dir: str, skip_existing: bool = True) -> dict:
        added = queued_before = skipped_done = requeued = 0
        now = time.time()
        for raw in items:
            it = self._clean(raw)
            if skip_existing and it["file_unique_id"] and self.is_downloaded(it["file_unique_id"], dest_dir):
                skipped_done += 1
                continue
            try:
                cur = self._exec(
                    """insert into queue (chat_id, chat_title, msg_id, file_unique_id, file_name, ext,
                                          size, dest_dir, status, created_at)
                       values (?,?,?,?,?,?,?,?,'pending',?)""",
                    (
                        it["chat_id"], it["chat_title"], it["msg_id"], it["file_unique_id"],
                        it["file_name"], it["ext"], it["size"], dest_dir, now,
                    ),
                )
                if cur.rowcount:
                    added += 1
                else:
                    queued_before += 1
            except sqlite3.IntegrityError:
                # Ya hay una fila para ese mensaje: si se ha pedido «forzar», se reencola.
                row = self._rows("select id, status from queue where chat_id=? and msg_id=?",
                                 (it["chat_id"], it["msg_id"]))
                if (not skip_existing) and row and row[0]["status"] in ("done", "error", "cancelled", "skipped"):
                    self._exec(
                        """update queue set status='pending', progress=0, error=null, tries=0, dest_dir=?,
                                  file_name=?, size=?, chat_title=?, started_at=null, finished_at=null
                           where id=?""",
                        (dest_dir, it["file_name"], it["size"], it["chat_title"], row[0]["id"]),
                    )
                    requeued += 1
                    continue
                queued_before += 1
        return {"added": added, "queued_before": queued_before, "skipped_done": skipped_done, "requeued": requeued}

    def next_pending(self) -> dict | None:
        rows = self._rows("select * from queue where status='pending' order by id limit 1")
        return rows[0] if rows else None

    def mark_running(self, qid: int) -> None:
        self._exec(
            "update queue set status='running', started_at=?, progress=0, error=null, tries=tries+1 where id=?",
            (time.time(), qid),
        )

    def finish(self, qid: int, status: str, error: str | None = None, progress: int | None = None) -> None:
        if progress is None:
            self._exec("update queue set status=?, error=?, finished_at=? where id=?", (status, error, time.time(), qid))
        else:
            self._exec(
                "update queue set status=?, error=?, progress=?, finished_at=? where id=?",
                (status, error, progress, time.time(), qid),
            )

    def set_progress(self, qid: int, progress: int) -> None:
        self._exec("update queue set progress=? where id=?", (int(progress), qid))

    def status_of(self, qid: int) -> str:
        rows = self._rows("select status from queue where id=?", (qid,))
        return rows[0]["status"] if rows else "gone"

    def item(self, qid: int) -> dict | None:
        rows = self._rows("select * from queue where id=?", (qid,))
        return rows[0] if rows else None

    def pending_tries(self, qid: int) -> int:
        rows = self._rows("select tries from queue where id=?", (qid,))
        return int(rows[0]["tries"]) if rows else 0

    def requeue(self, qid: int) -> None:
        self._exec(
            "update queue set status='pending', progress=0, error=null, started_at=null, finished_at=null where id=?",
            (qid,),
        )

    def cancel(self, qid: int) -> None:
        """Pendiente -> cancelado ya. En curso -> se corta en la siguiente marca de progreso."""
        self._exec(
            "update queue set status='cancelled', finished_at=?, error='cancelado por el usuario' "
            "where id=? and status in ('pending','running')",
            (time.time(), qid),
        )

    def remove(self, qid: int) -> None:
        self._exec("delete from queue where id=?", (qid,))

    def retry_failed(self) -> int:
        cur = self._exec(
            "update queue set status='pending', progress=0, error=null, tries=0, started_at=null, finished_at=null "
            "where status in ('error','cancelled','skipped')"
        )
        return cur.rowcount or 0

    def clear_finished(self) -> int:
        cur = self._exec("delete from queue where status in ('done','error','cancelled','skipped')")
        return cur.rowcount or 0

    def counts(self) -> dict:
        out = {"pending": 0, "running": 0, "done": 0, "error": 0, "cancelled": 0, "skipped": 0, "total": 0}
        for r in self._rows("select status, count(*) n from queue group by status"):
            out[r["status"]] = r["n"]
            out["total"] += r["n"]
        return out

    def queue(self, limit: int = 800, statuses: list[str] | None = None) -> list[dict]:
        if statuses:
            marks = ",".join("?" * len(statuses))
            return self._rows(
                f"select * from queue where status in ({marks}) order by id desc limit ?", (*statuses, limit)
            )
        return self._rows("select * from queue order by id desc limit ?", (limit,))

    # ---------- registro de descargas ----------
    def record_download(self, item: dict, path: str, size: int | None = None) -> None:
        uid = item.get("file_unique_id") or f"{item['chat_id']}:{item['msg_id']}"
        self._exec(
            """insert into manifest (file_unique_id, chat_id, chat_title, msg_id, file_name, size, path, downloaded_at)
               values (?,?,?,?,?,?,?,?)
               on conflict(file_unique_id) do update set
                 path=excluded.path, size=excluded.size, file_name=excluded.file_name,
                 downloaded_at=excluded.downloaded_at""",
            (
                uid, int(item["chat_id"]), item.get("chat_title") or "", int(item["msg_id"]),
                item["file_name"], int(size or item.get("size") or 0), path, time.time(),
            ),
        )

    def is_downloaded(self, file_unique_id: str, dest_dir: str | None = None) -> str | None:
        """Devuelve la ruta si ya está descargado y el fichero sigue existiendo."""
        if not file_unique_id:
            return None
        rows = self._rows("select path from manifest where file_unique_id=?", (file_unique_id,))
        for r in rows:
            p = r["path"]
            if Path(p).exists():
                return p
        return None

    def known_path(self, file_unique_id: str) -> str | None:
        """Ruta donde ya se descargó este fichero, si sigue existiendo."""
        return self.is_downloaded(file_unique_id)

    def manifest(self, q: str | None = None, limit: int = 500) -> list[dict]:
        if q:
            return self._rows(
                "select * from manifest where file_name like ? or path like ? order by downloaded_at desc limit ?",
                (f"%{q}%", f"%{q}%", limit),
            )
        return self._rows("select * from manifest order by downloaded_at desc limit ?", (limit,))

    def close(self) -> None:
        with self._lock:
            self._db.close()
