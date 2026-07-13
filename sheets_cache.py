"""
Wrapper transparente de caché TTL para gspread.
Envuelve gs_client sin modificar ningún otro archivo.
- get_all_values / get_all_records  → cacheados N segundos
- append_row / update / update_cell → invalidan caché automáticamente
"""

import time
import threading

_cache: dict = {}
_lock = threading.Lock()

DEFAULT_TTL = 90  # segundos — ajustar según necesidad


# ─────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────

def _get_or_fetch(key: str, fetch_fn, ttl: int):
    with _lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["ts"]) < ttl:
            return entry["data"]
    data = fetch_fn()
    with _lock:
        _cache[key] = {"data": data, "ts": time.time()}
    return data


def invalidate(prefix: str | None = None):
    with _lock:
        if prefix is None:
            _cache.clear()
        else:
            for k in [k for k in _cache if k.startswith(prefix)]:
                del _cache[k]


def cache_stats() -> dict:
    with _lock:
        return {"entries": len(_cache), "keys": list(_cache.keys())}


# ─────────────────────────────────────────────
# Wrappers
# ─────────────────────────────────────────────

_WRITE_METHODS = {
    "append_row", "append_rows", "update", "update_cell",
    "update_cells", "delete_rows", "insert_row", "insert_rows",
    "batch_update", "clear", "resize",
}


class CachedWorksheet:
    def __init__(self, ws, key: str, ttl: int = DEFAULT_TTL):
        self._ws = ws
        self._key = key
        self._ttl = ttl

    # Lecturas cacheadas
    def get_all_values(self, **kwargs):
        return _get_or_fetch(f"{self._key}:values", lambda: self._ws.get_all_values(**kwargs), self._ttl)

    def get_all_records(self, **kwargs):
        return _get_or_fetch(f"{self._key}:records", lambda: self._ws.get_all_records(**kwargs), self._ttl)

    def col_values(self, col, **kwargs):
        return _get_or_fetch(f"{self._key}:col{col}", lambda: self._ws.col_values(col, **kwargs), self._ttl)

    def row_values(self, row, **kwargs):
        return _get_or_fetch(f"{self._key}:row{row}", lambda: self._ws.row_values(row, **kwargs), self._ttl)

    # Escrituras → invalidan caché
    def __getattr__(self, name: str):
        attr = getattr(self._ws, name)
        if callable(attr) and name in _WRITE_METHODS:
            def _invalidating(*args, **kwargs):
                result = attr(*args, **kwargs)
                invalidate(self._key)
                return result
            return _invalidating
        return attr


class CachedSpreadsheet:
    def __init__(self, ss, name: str, ttl: int = DEFAULT_TTL):
        self._ss = ss
        self._name = name
        self._ttl = ttl
        self._ws_cache: dict = {}

    def worksheet(self, title: str) -> CachedWorksheet:
        if title not in self._ws_cache:
            ws = self._ss.worksheet(title)
            key = f"{self._name}::{title}"
            self._ws_cache[title] = CachedWorksheet(ws, key, self._ttl)
        return self._ws_cache[title]

    def __getattr__(self, name: str):
        return getattr(self._ss, name)


class CachedClient:
    """Reemplaza gs_client con caché transparente. Uso: app.gs_client = CachedClient(raw_client)"""

    def __init__(self, client, ttl: int = DEFAULT_TTL):
        self._client = client
        self._ttl = ttl
        self._ss_cache: dict = {}

    def open(self, name: str) -> CachedSpreadsheet:
        if name not in self._ss_cache:
            ss = self._client.open(name)
            self._ss_cache[name] = CachedSpreadsheet(ss, name, self._ttl)
        return self._ss_cache[name]

    def open_by_key(self, key: str) -> CachedSpreadsheet:
        ss = self._client.open_by_key(key)
        cache_key = f"__key__{key}"
        if cache_key not in self._ss_cache:
            self._ss_cache[cache_key] = CachedSpreadsheet(ss, cache_key, self._ttl)
        return self._ss_cache[cache_key]

    def __getattr__(self, name: str):
        return getattr(self._client, name)
