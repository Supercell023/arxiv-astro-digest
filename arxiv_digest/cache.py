"""Bound regenerable model/PDF caches without touching unrelated user files."""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def prune_cache(cache_dir: str, *, now: float | None = None, max_age_days: int = 30,
                max_bytes: int = 256 * 1024 * 1024) -> int:
    if not cache_dir:
        return 0
    try:
        root = Path(cache_dir).resolve()
    except (OSError, ValueError, RuntimeError):
        LOGGER.warning("缓存目录不可用，跳过回收。")
        return 0
    stamp = time.time() if now is None else now
    entries = []
    removed = 0
    for namespace in ("extract", "fulltext", "preprints"):
        directory = root / namespace
        if not directory.is_dir() or directory.is_symlink():
            continue
        for path in directory.glob("*.json"):
            if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name) or path.is_symlink():
                continue
            try:
                if not path.resolve().is_relative_to(root):
                    continue
                stat = path.stat()
                if stat.st_mtime < stamp - max_age_days * 86400:
                    path.unlink()
                    removed += 1
                else:
                    entries.append((stat.st_mtime, stat.st_size, path))
            except (OSError, RuntimeError):
                LOGGER.warning("缓存维护遇到不可读写文件，跳过。")
    size = sum(row[1] for row in entries)
    for _, length, path in sorted(entries):
        if size <= max_bytes:
            break
        try:
            path.unlink()
            size -= length
            removed += 1
        except OSError:
            LOGGER.warning("旧缓存无法回收，继续业务流程。")
    return removed
