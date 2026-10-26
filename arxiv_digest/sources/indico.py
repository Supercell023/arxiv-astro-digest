"""Indico 会议报告抓取。

Indico 是天文/物理界的事实标准日程系统，并且提供结构化导出——
不用爬 HTML，直接拿一场会议的全部报告标题、讲者与摘要：

    https://<host>/export/event/<event_id>.json?detail=contributions

代价是 `conferences.yaml` 里要维护 event id（一年改一次）。
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from ..fulltext import _ssl_context
from ..identity import stable_id
from .base import KIND_TALK, Item, SourceResult, plain_text

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


def export_url(event_url: str) -> str:
    """把 `https://host/event/1234` 变成它的 JSON 导出地址。"""

    cleaned = event_url.strip().rstrip("/")
    if "/event/" not in cleaned:
        return ""
    host, _, tail = cleaned.partition("/event/")
    event_id = tail.split("/")[0].split("?")[0]
    if not event_id.isdigit():
        return ""
    return f"{host}/export/event/{event_id}.json?detail=contributions"


def _start_label(contribution: dict[str, object]) -> str:
    start = contribution.get("startDate")
    if isinstance(start, dict):
        date = str(start.get("date") or "")
        time = str(start.get("time") or "")
        if date and time:
            return f"{date}T{time}"
        return date
    return ""


def _speakers(contribution: dict[str, object]) -> str:
    raw = contribution.get("speakers") or contribution.get("primaryauthors") or ()
    names: list[str] = []
    if isinstance(raw, (list, tuple)):
        for person in raw:
            if isinstance(person, dict):
                name = str(person.get("fullName") or person.get("name") or "").strip()
            else:
                name = str(person).strip()
            if name:
                names.append(name)
    return "、".join(names[:4])


def _absolute(url: str, event_url: str) -> str:
    """Indico 的报告 url 常是 `/event/361/contributions/1234/` 这种相对路径。"""

    if not url:
        return event_url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        base = event_url.split("/event/")[0] if "/event/" in event_url else event_url.rstrip("/")
        return f"{base}{url}"
    return url


def _flatten(payload: list[object] | tuple[object, ...]) -> list[dict[str, object]]:
    """导出的 `results` 可能直接是报告列表，也可能是"事件对象 + 内嵌 contributions"。

    两种都见过：`?detail=contributions` 在部分 Indico 版本上返回整场事件、
    把报告放在 `contributions` 里，所以这里统一摊平。
    """

    contributions: list[dict[str, object]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        nested = entry.get("contributions")
        if isinstance(nested, (list, tuple)) and nested:
            for raw in nested:
                if isinstance(raw, dict):
                    if not raw.get("url") and entry.get("url"):
                        raw = {**raw, "event_url": entry.get("url")}
                    contributions.append(raw)
            continue
        if entry.get("title"):
            contributions.append(entry)
    return contributions


def _contribution_to_item(
    contribution: dict[str, object], event_name: str, tags: tuple[str, ...], event_url: str = ""
) -> Item | None:
    title = str(contribution.get("title") or "").strip()
    if not title:
        return None
    url = _absolute(
        str(
            contribution.get("url")
            or contribution.get("material_url")
            or contribution.get("event_url")
            or ""
        ).strip(),
        event_url,
    )
    description = str(contribution.get("description") or "")
    speakers = _speakers(contribution)
    stamp = _start_label(contribution)
    return Item(
        id=stable_id("indico", urllib.parse.quote(event_name, safe=""),
                     str(contribution.get("id") or contribution.get("url") or title)),
        kind=KIND_TALK,
        title=plain_text(title, limit=180),
        url=url,
        source=event_name,
        published=stamp,
        text=plain_text(description, limit=420),
        tags=tags,
        meta={"讲者": speakers} if speakers else {},
    )


def fetch_event(
    event: dict[str, object],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_items: int = 8,
) -> SourceResult:
    """抓一场 Indico 会议的报告清单，失败只返回 error。"""

    name = str(event.get("name") or event.get("id") or "indico")
    event_url = str(event.get("url") or "")
    result = SourceResult(name=name, key=str(event.get("id") or event_url))

    target = export_url(event_url)
    if not target:
        result.error = "url 需形如 https://host/event/1234"
        return result

    try:
        request = urllib.request.Request(target, headers={"User-Agent": "arxiv-digest/1.0 (+indico)"})
        with urllib.request.urlopen(  # noqa: S310 - 地址来自本地配置
            request, timeout=timeout, context=_ssl_context()
        ) as response:
            data = response.read(5 * 1024 * 1024 + 1)
            if len(data) > 5 * 1024 * 1024:
                raise ValueError("Indico response exceeds 5 MiB")
            payload = json.loads(data)
    except Exception as exc:
        LOGGER.warning("Indico 抓取失败（%s）：%s", target, exc)
        result.error = f"导出失败：{exc}"
        return result

    raw_results = payload.get("results") if isinstance(payload, dict) else payload
    if not isinstance(raw_results, (list, tuple)):
        result.error = "导出格式不是 contributions 列表"
        return result

    contributions = _flatten(raw_results)
    if not contributions:
        result.error = "导出里没有报告（event id 对吗？）"
        return result
    contributions.sort(key=lambda raw: _start_label(raw) or "9999")

    tags = tuple(str(tag) for tag in (event.get("tags") or ()))  # type: ignore[union-attr]
    for raw in contributions[:max_items]:
        item = _contribution_to_item(raw, name, tags, event_url)
        if item is not None:
            result.items.append(item)
    return result


def fetch_events(
    events: list[dict[str, object]],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_items: int = 8,
) -> list[SourceResult]:
    return [fetch_event(event, timeout=timeout, max_items=max_items) for event in events]
