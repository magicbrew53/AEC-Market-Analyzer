"""
Downloads ENR data files from Vercel Blob to a local cache directory.
Called once at startup; subsequent runs use the cached files.
"""

from __future__ import annotations

import os
import json
import hashlib
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

BLOB_BASE = "https://blob.vercel-storage.com"
ENR_PREFIX = "enr-data/"
STATIC_FILES = ["cci.xlsx", "fmi_forecast.json"]


def _blob_token() -> str:
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
    if not token:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN env var not set")
    return token


def _headers() -> dict:
    return {"Authorization": f"Bearer {_blob_token()}"}


def list_blobs(prefix: str) -> list[dict]:
    """Return list of blob objects matching prefix."""
    resp = requests.get(
        "https://blob.vercel-storage.com",
        headers=_headers(),
        params={"prefix": prefix},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("blobs", [])


def download_blob(url: str, dest_path: Path) -> None:
    """Download a single blob URL to dest_path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)


def _etag_cache_path(dest: Path) -> Path:
    return dest.with_suffix(dest.suffix + ".etag")


def _needs_download(dest: Path, remote_etag: str | None) -> bool:
    if not dest.exists():
        return True
    if not remote_etag:
        return False  # No etag available; trust local copy
    etag_file = _etag_cache_path(dest)
    if not etag_file.exists():
        return True
    return etag_file.read_text().strip() != remote_etag.strip()


def _save_etag(dest: Path, etag: str) -> None:
    _etag_cache_path(dest).write_text(etag)


def sync_enr_files(data_dir: Path) -> None:
    """Download all ENR xlsx files from Vercel Blob into data_dir/enr/."""
    enr_dir = data_dir / "enr"
    enr_dir.mkdir(parents=True, exist_ok=True)

    blobs = list_blobs(ENR_PREFIX)
    if not blobs:
        logger.warning("No ENR blobs found in Vercel Blob under prefix '%s'", ENR_PREFIX)
        return

    for blob in blobs:
        filename = blob["pathname"].removeprefix(ENR_PREFIX)
        if not filename:
            continue
        dest = enr_dir / filename
        etag = blob.get("etag")
        if _needs_download(dest, etag):
            logger.info("Downloading %s ...", filename)
            download_blob(blob["url"], dest)
            if etag:
                _save_etag(dest, etag)
        else:
            logger.debug("Cached: %s", filename)


def sync_static_files(data_dir: Path) -> None:
    """Download cci.xlsx and fmi_forecast.json from Vercel Blob."""
    for filename in STATIC_FILES:
        dest = data_dir / filename
        blobs = list_blobs(f"static-data/{filename}")
        if not blobs:
            if dest.exists():
                logger.debug("No blob for %s, using local copy", filename)
            else:
                logger.warning("Missing %s — not in Vercel Blob and not on disk", filename)
            continue
        blob = blobs[0]
        etag = blob.get("etag")
        if _needs_download(dest, etag):
            logger.info("Downloading %s ...", filename)
            download_blob(blob["url"], dest)
            if etag:
                _save_etag(dest, etag)


def ensure_data(data_dir: Path) -> None:
    """
    Entry point called at startup. Downloads all remote data files that are
    missing or stale. Uses etag-based caching so restarts are fast.
    """
    logger.info("Syncing data files from Vercel Blob...")
    sync_enr_files(data_dir)
    sync_static_files(data_dir)
    logger.info("Data sync complete.")