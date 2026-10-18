"""Configuración: config.toml (o config.json) junto al proyecto. El entorno puede sobreescribir."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as _toml  # type: ignore
    except ModuleNotFoundError:
        _toml = None

BASE = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "telegram": {"api_id": 0, "api_hash": "", "session": "data/tg"},
    "downloads": {
        "root": "./descargas",
        "subfolder_por_grupo": True,
        "skip_existing": True,
        "max_retries": 3,
    },
    "server": {"host": "127.0.0.1", "port": 8765},
}


def _merge(base: dict, over: dict) -> dict:
    out = deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | None = None) -> dict:
    cfg = deepcopy(DEFAULTS)
    if path:
        p = Path(path).expanduser()
    else:
        p = next((BASE / n for n in ("config.toml", "config.json") if (BASE / n).exists()), None)
    if p and Path(p).exists():
        if Path(p).suffix.lower() == ".json":
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        else:
            if _toml is None:
                raise RuntimeError(
                    "config.toml necesita Python 3.11+ (tomllib). Usa Python 3.11 o superior, "
                    "o `pip install tomli`, o pasa la configuración como config.json."
                )
            data = _toml.loads(Path(p).read_text(encoding="utf-8"))
        cfg = _merge(cfg, data)
    # el entorno manda (útil para lanzarlo sin fichero)
    if os.environ.get("TD_API_ID"):
        cfg["telegram"]["api_id"] = int(os.environ["TD_API_ID"])
    if os.environ.get("TD_API_HASH"):
        cfg["telegram"]["api_hash"] = os.environ["TD_API_HASH"]
    if os.environ.get("TD_SESSION"):
        cfg["telegram"]["session"] = os.environ["TD_SESSION"]
    if os.environ.get("TD_DATA_DIR"):
        cfg["_data_dir"] = os.environ["TD_DATA_DIR"]
    return cfg


def data_dir(cfg: dict) -> Path:
    d = Path(cfg.get("_data_dir") or (BASE / "data")).expanduser()
    if not d.is_absolute():
        d = BASE / d
    d.mkdir(parents=True, exist_ok=True)
    return d


def downloads_root(cfg: dict) -> Path:
    """Carpeta destino por defecto, resuelta contra la carpeta del programa si es relativa."""
    p = Path(str(cfg["downloads"]["root"])).expanduser()
    return p if p.is_absolute() else (BASE / p)


def session_path(cfg: dict) -> Path:
    s = Path(cfg["telegram"]["session"]).expanduser()
    if not s.is_absolute():
        s = BASE / s
    return s


def session_file(cfg: dict) -> Path:
    """Telethon añade '.session' si el nombre no lo lleva ya."""
    s = session_path(cfg)
    return s if s.name.endswith(".session") else s.with_name(s.name + ".session")


def api_credentials(cfg: dict) -> tuple[int, str]:
    return int(cfg["telegram"]["api_id"] or 0), str(cfg["telegram"]["api_hash"] or "")
