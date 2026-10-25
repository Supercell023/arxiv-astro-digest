"""多源收集入口：arXiv 之外的世界（二手解读、会议报告）。

设计约束（来自真实失败）：**任何单个源挂掉都不能影响其它源，更不能影响发信。**
所以每个 fetcher 都只返回 `SourceResult`，错误写在 `error` 里。
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from pathlib import Path

import yaml

from .base import KIND_PAPER, KIND_SECONDARY, KIND_TALK, Item, SourceResult, dedupe
from .indico import fetch_events
from .rss import fetch_feeds

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_ITEMS = 6
DEFAULT_TIMEOUT = 30


def load_sources(path: str | Path) -> dict[str, list[dict[str, object]]]:
    """读 `config/sources.yaml`。文件缺失或写坏都返回空配置。"""

    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        LOGGER.info("未找到信息源配置 %s，跳过二手源", path)
        return {"feeds": [], "indico": []}
    except yaml.YAMLError as exc:
        LOGGER.warning("信息源配置解析失败（%s）：%s", path, exc)
        return {"feeds": [], "indico": []}

    if not isinstance(raw, dict):
        return {"feeds": [], "indico": []}

    def section(key: str) -> list[dict[str, object]]:
        value = raw.get(key) or []
        if not isinstance(value, list):
            return []
        return [entry for entry in value if isinstance(entry, dict) and entry.get("enabled", True) is not False]

    return {"feeds": section("feeds"), "indico": section("indico")}


def _wanted(entry: dict[str, object], fields: tuple[str, ...]) -> bool:
    """源可以声明 `fields: [galaxy_cosmology]`，只在勾选了该领域时启用。"""

    declared = tuple(str(tag) for tag in (entry.get("fields") or ()))  # type: ignore[union-attr]
    if not declared:
        return True
    if not fields:
        return True
    return bool(set(declared) & set(fields))


def feed_cadence(entry: dict[str, object]) -> str:
    """抓取节奏：`daily`（默认）或 `weekly`。

    这是给"更新不频繁的官网"用的：望远镜/巡天项目页一周发不了一条，
    每天抓就是每天浪费一次请求，还容易触发对方限流。
    """

    value = str(entry.get("cadence") or "daily").strip().lower()
    return value if value in {"daily", "weekly"} else "daily"


def source_ids(path: str | Path) -> dict[str, str]:
    """id -> cadence。ID 的推导规则必须与 `fetch_feed` / `fetch_event` 一致。"""

    config = load_sources(path)
    mapping: dict[str, str] = {}
    for feed in config["feeds"]:
        key = str(feed.get("id") or feed.get("name") or feed.get("url") or "")
        if key:
            mapping[key] = feed_cadence(feed)
    for event in config["indico"]:
        key = str(event.get("id") or event.get("url") or "")
        if key:
            mapping[key] = feed_cadence(event)
    return mapping


def collect_sources(
    path: str | Path,
    *,
    fields: tuple[str, ...] = (),
    max_items: int = DEFAULT_MAX_ITEMS,
    timeout: int = DEFAULT_TIMEOUT,
    skip_ids: Collection[str] = (),
) -> list[SourceResult]:
    """抓所有启用的源。顺序稳定：先 RSS，后 Indico。

    `skip_ids` 是"今天不该抓"的源 id（按 `cadence` 由状态文件算出来）。
    跳过的源不出现在返回值里，也就不会被记为"已抓取"。
    """

    config = load_sources(path)
    skip = set(skip_ids)
    feeds = [
        feed
        for feed in config["feeds"]
        if _wanted(feed, fields) and str(feed.get("id") or feed.get("name") or feed.get("url")) not in skip
    ]
    events = [
        event
        for event in config["indico"]
        if _wanted(event, fields) and str(event.get("id") or event.get("url")) not in skip
    ]

    results: list[SourceResult] = []
    if feeds:
        results.extend(fetch_feeds(feeds, timeout=timeout, max_items=max_items))
    if events:
        results.extend(fetch_events(events, timeout=timeout, max_items=max_items))
    return results


def merge_items(results: list[SourceResult], *, kind: str | None = None) -> list[Item]:
    """汇总所有源的成功条目并去重；按发布时间倒序（无时间的排最后）。"""

    items: list[Item] = []
    for result in results:
        items.extend(result.items)
    if kind is not None:
        items = [item for item in items if item.kind == kind]
    deduped = dedupe(items)
    deduped.sort(key=lambda item: (item.published or "", item.title), reverse=True)
    return deduped


__all__ = [
    "DEFAULT_MAX_ITEMS",
    "DEFAULT_TIMEOUT",
    "KIND_PAPER",
    "KIND_SECONDARY",
    "KIND_TALK",
    "Item",
    "SourceResult",
    "collect_sources",
    "feed_cadence",
    "load_sources",
    "merge_items",
    "source_ids",
]
