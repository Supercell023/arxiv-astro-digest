"""RSS / Atom 抓取。

只用标准库：urllib + xml.etree。项目没有 requests/feedparser，
保持零新增依赖，GitHub Actions 上也不会多一个会变的第三方解析器。
"""

from __future__ import annotations

import logging
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from ..fulltext import _ssl_context
from ..identity import stable_id
from .base import KIND_SECONDARY, Item, SourceResult, plain_text

LOGGER = logging.getLogger(__name__)

_ATOM = "{http://www.w3.org/2005/Atom}"
_DC_DATE = "{http://purl.org/dc/elements/1.1/}date"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"

DEFAULT_TIMEOUT = 30


def _parse_date(raw: str) -> str:
    """RSS 用 RFC 822，Atom 用 ISO 8601，两种都试。失败返回空串。"""
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        return parsedate_to_datetime(text).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, IndexError):
        pass
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).astimezone().isoformat(timespec="seconds")
        except ValueError:
            continue
    return ""


def _entries(root: ET.Element) -> list[ET.Element]:
    channel = root.find("channel")
    if channel is not None:
        return channel.findall("item")
    return root.findall(f"{_ATOM}entry")


def _entry_to_item(
    entry: ET.Element, feed_id: str, source_name: str, tags: tuple[str, ...]
) -> Item | None:
    title = (entry.findtext("title") or entry.findtext(f"{_ATOM}title") or "").strip()
    if not title:
        return None

    link = (entry.findtext("link") or "").strip()
    if not link:
        node = entry.find(f"{_ATOM}link")
        link = (node.get("href") if node is not None else "") or ""

    raw_date = (
        entry.findtext("pubDate")
        or entry.findtext(f"{_ATOM}published")
        or entry.findtext(f"{_ATOM}updated")
        or entry.findtext(_DC_DATE)
        or ""
    )
    published = _parse_date(raw_date)

    raw_body = (
        entry.findtext("description")
        or entry.findtext(f"{_ATOM}summary")
        or entry.findtext(_CONTENT)
        or ""
    )
    body = plain_text(raw_body or "", limit=420)

    return Item(
        id=stable_id("rss", feed_id, entry.findtext("guid") or entry.findtext(f"{_ATOM}id") or link or title),
        kind=KIND_SECONDARY,
        title=plain_text(title, limit=160),
        url=link,
        source=source_name,
        published=published,
        text=body,
        tags=tags,
    )


def fetch_feed(
    feed: dict[str, object],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_items: int = 6,
) -> SourceResult:
    """抓一个 RSS/Atom 源。任何异常都收敛成 `SourceResult.error`。"""

    name = str(feed.get("name") or feed.get("id") or feed.get("url") or "feed")
    url = str(feed.get("url") or "")
    feed_id = str(feed.get("id") or name)
    result = SourceResult(name=name, key=feed_id)
    if not url:
        result.error = "配置缺少 url"
        return result

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "arxiv-digest/1.0 (+rss)"})
        with urllib.request.urlopen(  # noqa: S310 - 地址来自本地配置
            request, timeout=timeout, context=_ssl_context()
        ) as response:
            payload = response.read(5 * 1024 * 1024 + 1)
            if len(payload) > 5 * 1024 * 1024:
                raise ValueError("RSS exceeds 5 MiB")
    except Exception as exc:
        LOGGER.warning("RSS 抓取失败（%s）：%s", url, exc)
        result.error = f"下载失败：{exc}"
        return result

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        LOGGER.warning("RSS 解析失败（%s）：%s", url, exc)
        result.error = f"解析失败：{exc}"
        return result

    feed_id = str(feed.get("id") or name)
    tags = tuple(str(tag) for tag in (feed.get("tags") or ()))  # type: ignore[union-attr]
    exclude = tuple(str(word).lower() for word in (feed.get("exclude") or ()) if str(word).strip())
    raw_limit = feed.get("max_items")
    limit = int(raw_limit) if isinstance(raw_limit, (int, str)) and str(raw_limit).isdigit() else max_items

    for entry in _entries(root)[: limit * 3 if exclude else limit]:
        item = _entry_to_item(entry, feed_id, name, tags)
        if item is None:
            continue
        # 有的源会把每日一图、招聘之类混在新闻流里，配置里写 `exclude` 挡掉
        if exclude and any(word in item.title.lower() for word in exclude):
            continue
        domain = str(feed.get("domain") or "astronomy")
        item.meta["domain"] = domain
        result.items.append(item)
        if len(result.items) >= limit:
            break
    return result


def fetch_feeds(
    feeds: list[dict[str, object]],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_items: int = 6,
) -> list[SourceResult]:
    return [fetch_feed(feed, timeout=timeout, max_items=max_items) for feed in feeds]
