"""Auto-download the Apple docset from a GitHub release.

The server stays offline at runtime — this module only runs once, on
first start when no local docset can be found, to populate the cache
directory. Subsequent starts find the cached copy via
``find_docset()`` and never hit the network.

Disable with ``APPLEREF_AUTO_DOWNLOAD=0``.

Resolution:
  1. ``APPLEREF_RELEASE_TAG`` env var (e.g. ``docset-24500-79``)
  2. Latest release whose tag starts with ``docset-``
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_REPO = "nzrsky/appleref-mcp"
TAG_PREFIX = "docset-"
ASSET_PREFIX = "appleref-docset-"
ASSET_SUFFIX = ".tar.xz"
USER_AGENT = "appleref-mcp/auto-download"


def cache_dir() -> Path:
    """Where the downloaded docset lives.

    ``APPLEREF_CACHE_DIR`` overrides; otherwise ``~/.cache/appleref-mcp``
    on every platform (good enough — macOS users don't care).
    """
    env = os.environ.get("APPLEREF_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "appleref-mcp"


def cached_docset_path() -> Path:
    return cache_dir() / "Apple_API_Reference.docset"


def auto_download_enabled() -> bool:
    return os.environ.get("APPLEREF_AUTO_DOWNLOAD", "1") != "0"


def _log(msg: str) -> None:
    # MCP servers speak JSON-RPC on stdout — diagnostics must go to stderr.
    print(f"[appleref-mcp] {msg}", file=sys.stderr, flush=True)


def _http_get_json(url: str, *, token: str | None = None) -> Any:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _http_download(url: str, dest: Path, *, expected_size: int | None = None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as out:
        total = expected_size or int(resp.headers.get("Content-Length") or 0)
        read = 0
        last_pct = -1
        chunk = 1 << 20  # 1 MiB
        while True:
            buf = resp.read(chunk)
            if not buf:
                break
            out.write(buf)
            read += len(buf)
            if total:
                pct = int(read * 100 / total)
                if pct != last_pct and pct % 5 == 0:
                    _log(f"  downloaded {read >> 20} / {total >> 20} MiB ({pct}%)")
                    last_pct = pct


def _resolve_release(repo: str) -> dict[str, Any]:
    """Return the GitHub release JSON to download from."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    tag = os.environ.get("APPLEREF_RELEASE_TAG")
    if tag:
        url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
        return _http_get_json(url, token=token)

    # Walk releases newest-first, return first docset-* tag.
    url = f"https://api.github.com/repos/{repo}/releases?per_page=30"
    releases = _http_get_json(url, token=token)
    for rel in releases:
        if str(rel.get("tag_name", "")).startswith(TAG_PREFIX):
            return rel
    raise RuntimeError(
        f"no release with tag prefix {TAG_PREFIX!r} found in {repo}"
    )


def _pick_assets(release: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    archive_asset: dict[str, Any] | None = None
    sha_asset: dict[str, Any] | None = None
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.startswith(ASSET_PREFIX) and name.endswith(ASSET_SUFFIX):
            archive_asset = asset
        elif name.startswith(ASSET_PREFIX) and name.endswith(".sha256"):
            sha_asset = asset
    if archive_asset is None:
        raise RuntimeError(
            f"release {release.get('tag_name')!r} has no {ASSET_PREFIX}*{ASSET_SUFFIX} asset"
        )
    return archive_asset, sha_asset


def _verify_sha256(archive: Path, expected_hex: str) -> None:
    h = hashlib.sha256()
    with archive.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual.lower() != expected_hex.lower():
        raise RuntimeError(
            f"sha256 mismatch for {archive.name}: expected {expected_hex}, got {actual}"
        )


def _safe_extract(archive: Path, dest_parent: Path) -> Path:
    """Extract the docset to dest_parent, returning the new docset path.

    Uses an atomic rename: extract to a sibling tmp dir, then swap in.
    """
    dest_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest_parent, prefix=".extract-") as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(archive, mode="r:xz") as tar:
            # PEP 706 'data' filter rejects path traversal, absolute paths,
            # symlinks escaping the dest, and special files. Default in 3.14.
            # Pre-3.12 fall back to a manual prefix check.
            if hasattr(tarfile, "data_filter"):
                tar.extractall(tmp_path, filter="data")
            else:
                base = str(tmp_path.resolve())
                for member in tar.getmembers():
                    resolved = str((tmp_path / member.name).resolve())
                    if resolved != base and not resolved.startswith(base + os.sep):
                        raise RuntimeError(f"refusing path-traversal entry: {member.name!r}")
                tar.extractall(tmp_path)  # noqa: S202 — entries validated above

        extracted = tmp_path / "Apple_API_Reference.docset"
        if not extracted.is_dir():
            raise RuntimeError("archive did not contain Apple_API_Reference.docset/")

        final = dest_parent / "Apple_API_Reference.docset"
        if final.exists():
            shutil.rmtree(final)
        shutil.move(str(extracted), str(final))
        return final


def ensure_docset(*, repo: str | None = None) -> Path:
    """Download + extract the docset into the cache dir if not already there.

    Returns the path to the ready-to-use .docset directory.
    Raises on any failure — callers should let it propagate; the MCP server
    will surface a clear error to the client.
    """
    cached = cached_docset_path()
    if (cached / "Contents/Resources/optimizedIndex.dsidx").is_file():
        return cached

    repo = repo or os.environ.get("APPLEREF_RELEASE_REPO") or DEFAULT_REPO
    _log(f"no local docset found; fetching from github.com/{repo}")

    release = _resolve_release(repo)
    archive_asset, sha_asset = _pick_assets(release)
    tag = release.get("tag_name", "?")
    _log(f"selected release {tag}: {archive_asset['name']} "
         f"({archive_asset.get('size', 0) >> 20} MiB)")

    target_parent = cache_dir()
    target_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=target_parent, prefix=".download-") as tmp:
        tmp_path = Path(tmp)
        archive_path = tmp_path / archive_asset["name"]
        _http_download(
            archive_asset["browser_download_url"],
            archive_path,
            expected_size=archive_asset.get("size"),
        )

        if sha_asset is not None:
            sha_path = tmp_path / sha_asset["name"]
            _http_download(sha_asset["browser_download_url"], sha_path)
            expected = sha_path.read_text().split()[0].strip()
            _log("verifying sha256…")
            _verify_sha256(archive_path, expected)
        else:
            _log("no .sha256 sibling asset; skipping checksum verification")

        _log(f"extracting to {target_parent}")
        _safe_extract(archive_path, target_parent)

    _log(f"docset ready at {cached}")
    return cached


@contextlib.contextmanager
def _suppress_urlerror_noise():
    """Make urllib errors readable in the MCP server logs."""
    try:
        yield
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"github API HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e.reason}") from e


def try_ensure_docset() -> Path | None:
    """Best-effort wrapper used by ``find_docset()`` — returns None on failure."""
    if not auto_download_enabled():
        return None
    try:
        with _suppress_urlerror_noise():
            return ensure_docset()
    except Exception as e:  # noqa: BLE001 — caller logs and falls through
        _log(f"auto-download failed: {e}")
        return None
