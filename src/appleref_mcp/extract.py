"""Locate and read an Apple_API_Reference.docset (Dash-format).

The docset is a directory containing:
  Contents/Resources/optimizedIndex.dsidx       — SQLite + FTS4 search index
  Contents/Resources/Documents/cache.db          — SQLite mapping uuid → (data_id, offset, length)
  Contents/Resources/Documents/fs/<data_id>      — brotli-compressed concatenation of DocC RenderJSON

Lookup pipeline:
  index 'GridItem' → request_key 'ls/documentation/swiftui/griditem'
  → canonical '/documentation/swiftui/griditem'
  → uuid = lang_prefix + base64url(sha1(canonical)[:6]).rstrip('=')
  → cache.db.refs WHERE uuid=? → (data_id, offset, length)
  → brotli.decompress(fs/<data_id>)[offset:offset+length] = JSON page
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

LANG_PREFIX = {"swift": "ls", "objc": "lc"}
LANG_FRAGMENT = {"swift": "<dash_entry_language=swift>", "objc": "<dash_entry_language=occ>"}


def _like_escape(s: str) -> str:
    """Escape SQL LIKE special characters using `!` as escape char.

    `_` and `%` are treated as literals after escaping; `!` is doubled.
    Used together with ``LIKE ... ESCAPE '!'``.
    """
    return s.replace("!", "!!").replace("_", "!_").replace("%", "!%")


class DocsetNotFound(RuntimeError):
    pass


def find_docset() -> Path:
    """Locate Apple_API_Reference.docset.

    Resolution order:
      1. APPLEREF_DOCSET env var (path to the .docset directory itself,
         OR to its parent — both accepted)
      2. ./Apple_API_Reference.docset (cwd)
      3. ~/Library/Application Support/Dash/DocSets/Apple_API_Reference/Apple_API_Reference.docset
      4. ~/Apple_API_Reference.docset
    """
    candidates: list[Path] = []
    env = os.environ.get("APPLEREF_DOCSET")
    if env:
        p = Path(env).expanduser()
        candidates.append(p)
        candidates.append(p / "Apple_API_Reference.docset")

    candidates += [
        Path.cwd() / "Apple_API_Reference.docset",
        Path.home() / "Library/Application Support/Dash/DocSets/Apple_API_Reference/Apple_API_Reference.docset",
        Path.home() / "Apple_API_Reference.docset",
    ]

    for c in candidates:
        if (c / "Contents/Resources/optimizedIndex.dsidx").is_file():
            return c

    msg = (
        "Could not find Apple_API_Reference.docset.\n"
        "Set APPLEREF_DOCSET to the .docset directory, or place it at one of:\n"
        + "\n".join(f"  - {c}" for c in candidates)
    )
    raise DocsetNotFound(msg)


@dataclass(frozen=True)
class IndexHit:
    name: str
    type: str  # 'Class' | 'Struct' | 'Method' | …
    path: str  # raw dash-apple-api://… url
    request_key: str  # e.g. 'documentation/swiftui/griditem'
    language: str  # 'swift' | 'objc'

    @property
    def canonical_path(self) -> str:
        return "/" + self.request_key

    @property
    def framework(self) -> str | None:
        # request_key looks like 'documentation/<framework>/...'
        parts = self.request_key.split("/")
        if len(parts) >= 2 and parts[0] == "documentation":
            return parts[1]
        return None


def _parse_path(raw_path: str) -> tuple[str, str] | None:
    """Extract (request_key, language) from a dash-apple-api:// path.

    Returns None if the path doesn't match the expected shape.
    """
    if "request_key=" not in raw_path:
        return None
    rk = raw_path.split("request_key=", 1)[1].split("#", 1)[0]
    if "&" in rk:
        rk = rk.split("&", 1)[0]
    # request_key has form '<langPrefix>/<canonical>' e.g. 'ls/documentation/...'
    if rk.startswith("ls/"):
        lang, canonical_rk = "swift", rk[3:]
    elif rk.startswith("lc/"):
        lang, canonical_rk = "objc", rk[3:]
    else:
        # rare: no lang prefix → assume swift
        lang, canonical_rk = "swift", rk
    # also infer language from fragment if present (more authoritative for dup rows)
    if "<dash_entry_language=swift>" in raw_path:
        lang = "swift"
    elif "<dash_entry_language=occ>" in raw_path:
        lang = "objc"
    return canonical_rk, lang


class Docset:
    """Read-only accessor for a Dash Apple_API_Reference.docset."""

    def __init__(self, root: Path):
        self.root = root
        self.index_db = root / "Contents/Resources/optimizedIndex.dsidx"
        self.cache_db = root / "Contents/Resources/Documents/cache.db"
        self.fs_dir = root / "Contents/Resources/Documents/fs"
        if not self.index_db.is_file():
            raise DocsetNotFound(f"missing optimizedIndex.dsidx in {root}")
        if not self.cache_db.is_file():
            raise DocsetNotFound(f"missing cache.db in {root}")
        if not self.fs_dir.is_dir():
            raise DocsetNotFound(f"missing fs/ in {root}")

    # ------------------------------------------------------------------ index
    def _index_conn(self) -> sqlite3.Connection:
        # read-only mode so we never mutate Dash's data
        uri = f"file:{self.index_db}?mode=ro"
        return sqlite3.connect(uri, uri=True)

    def _cache_conn(self) -> sqlite3.Connection:
        uri = f"file:{self.cache_db}?mode=ro"
        return sqlite3.connect(uri, uri=True)

    def search(
        self,
        query: str,
        *,
        language: str = "swift",
        framework: str | None = None,
        type: str | None = None,
        limit: int = 25,
    ) -> list[IndexHit]:
        """Search the docset index by name and canonical path.

        Match strategy (any of):
          - name = query (exact, case-sensitive) — score 0
          - name = query (case-insensitive) — score 1
          - name LIKE 'query%' — score 2
          - name LIKE '%query%' — score 3
          - canonical path contains lower(query) — score 4

        The path-substring fallback matters because Apple's index stores ObjC
        selectors as the `name` for many bridged APIs even on Swift rows, while
        the Swift API name is embedded in the canonical URL. Searching for
        `present(_:animated:completion:)` therefore only hits via path.

        Results are de-duplicated by (canonical_path, language).
        """
        if not query:
            return []
        if language not in LANG_PREFIX:
            raise ValueError(f"language must be 'swift' or 'objc', got {language!r}")
        lang_frag = LANG_FRAGMENT[language]

        q_low = query.lower()
        q_esc = _like_escape(query)
        like_prefix = q_esc + "%"
        like_any = "%" + q_esc + "%"

        where = ["instr(path, :lang_frag) > 0"]
        bind: dict[str, Any] = {"lang_frag": lang_frag}

        if framework:
            where.append("path LIKE :fw_pat ESCAPE '!'")
            bind["fw_pat"] = f"%/documentation/{_like_escape(framework.lower())}/%"
        if type:
            where.append("type = :typ")
            bind["typ"] = type

        where.append(
            "("
            "  name = :q"
            "  OR name LIKE :prefix ESCAPE '!' COLLATE NOCASE"
            "  OR name LIKE :contains ESCAPE '!' COLLATE NOCASE"
            "  OR instr(lower(path), :qlow) > 0"
            ")"
        )
        bind |= {"q": query, "qlow": q_low, "prefix": like_prefix, "contains": like_any}

        sql = f"""
            SELECT name, type, path,
                CASE
                    WHEN name = :q THEN 0
                    WHEN lower(name) = :qlow THEN 1
                    WHEN name LIKE :prefix ESCAPE '!' COLLATE NOCASE THEN 2
                    WHEN name LIKE :contains ESCAPE '!' COLLATE NOCASE THEN 3
                    ELSE 4
                END AS score
            FROM searchIndex
            WHERE {' AND '.join(where)}
            ORDER BY score, length(name), name
            LIMIT :lim
        """
        bind["lim"] = limit * 4

        seen: set[tuple[str, str]] = set()
        hits: list[IndexHit] = []
        with self._index_conn() as con:
            for name, typ, path, _score in con.execute(sql, bind):
                parsed = _parse_path(path)
                if parsed is None:
                    continue
                rk, lang = parsed
                if lang != language:
                    continue
                key = (rk, lang)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(IndexHit(name=name, type=typ, path=path, request_key=rk, language=lang))
                if len(hits) >= limit:
                    break
        return hits

    def list_frameworks(self, *, filter_text: str | None = None) -> list[str]:
        sql = "SELECT DISTINCT name FROM searchIndex WHERE type = 'Framework'"
        params: list[Any] = []
        if filter_text:
            sql += " AND name LIKE ? COLLATE NOCASE"
            params.append(f"%{filter_text}%")
        sql += " ORDER BY name"
        with self._index_conn() as con:
            return [row[0] for row in con.execute(sql, params)]

    def list_types(self) -> list[tuple[str, int]]:
        with self._index_conn() as con:
            return list(con.execute(
                "SELECT type, COUNT(*) FROM searchIndex GROUP BY type ORDER BY 2 DESC"
            ))

    # ------------------------------------------------------------------ content
    def fetch_by_request_key(self, request_key: str, *, language: str = "swift") -> dict[str, Any] | None:
        if language not in LANG_PREFIX:
            raise ValueError(f"language must be 'swift' or 'objc', got {language!r}")
        canonical = "/" + request_key.lstrip("/")
        digest = hashlib.sha1(canonical.encode("utf-8")).digest()[:6]
        suffix = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        uuid = LANG_PREFIX[language] + suffix

        with self._cache_conn() as con:
            row = con.execute(
                "SELECT data_id, offset, length FROM refs WHERE uuid = ?",
                (uuid,),
            ).fetchone()
        if row is None:
            return None
        data_id, offset, length = row
        return self._read_fs(data_id, offset, length)

    def _read_fs(self, data_id: int, offset: int, length: int) -> dict[str, Any] | None:
        decompressed = _decompress_fs(self.fs_dir, data_id)
        if decompressed is None:
            return None
        chunk = decompressed[offset : offset + length]
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            return None

    def fetch_first_match(
        self,
        query: str,
        *,
        language: str = "swift",
        framework: str | None = None,
        type: str | None = None,
    ) -> tuple[IndexHit, dict[str, Any]] | None:
        for hit in self.search(query, language=language, framework=framework, type=type, limit=8):
            doc = self.fetch_by_request_key(hit.request_key, language=language)
            if doc is not None:
                return hit, doc
        return None


@lru_cache(maxsize=8)
def _decompress_fs(fs_dir: Path, data_id: int) -> bytes | None:
    """Read fs/<data_id>, brotli-decompress, cache in memory.

    fs files are large (tens of MB decompressed) but few; LRU keeps recent ones hot.
    """
    import brotli  # imported lazily so test discovery works without the dep

    path = fs_dir / str(data_id)
    if not path.is_file():
        return None
    return brotli.decompress(path.read_bytes())
