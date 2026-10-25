"""多源条目的最小公共契约。

所有源——arXiv 论文、二手解读、会议报告——都产出 `Item`，
这样去重、状态过滤、渲染都只写一遍。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_SENTENCE_END = ("。", "！", "？", ". ", "! ", "? ", "; ", "；")

# kind 的取值有限，渲染时按它分组
KIND_PAPER = "paper"
KIND_SECONDARY = "secondary"
KIND_TALK = "talk"
KIND_EVENT = "event"


def plain_text(raw: str, *, limit: int = 420) -> str:
    """去 HTML 标签、反转义实体、压空白，并在句子边界截断。"""
    text = _TAG_RE.sub(" ", html.unescape(raw or ""))
    text = _SPACE_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = max(window.rfind(mark) for mark in _SENTENCE_END)
    if cut < limit // 2:
        cut = window.rfind(" ")
    if cut <= 0:
        cut = limit
    return window[:cut].rstrip(" ,;，；") + "…"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="minutes")


@dataclass
class Item:
    """一条可渲染的条目。"""

    id: str
    kind: str
    title: str
    url: str
    source: str
    published: str = ""
    text: str = ""
    tags: tuple[str, ...] = ()
    meta: dict[str, str] = field(default_factory=dict)
    title_zh: str = ""
    text_zh: str = ""

    @property
    def day(self) -> str:
        return self.published[:10]

    @property
    def is_fresh(self) -> bool:
        return bool(self.published)


@dataclass
class SourceResult:
    """一个源的一次抓取结果——成功失败都返回，绝不抛。"""

    name: str
    items: list[Item] = field(default_factory=list)
    error: str = ""
    fetched_at: str = field(default_factory=_now_iso)
    # 配置里的源标识，用于回写"上次抓取日期"；空字符串表示不参与节奏控制
    key: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def status_label(self) -> str:
        if self.ok:
            return f"{len(self.items)} 条"
        return f"失败（{self.error[:40]}）"


def _title_key(title: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", title.lower())


def dedupe(items: list[Item]) -> list[Item]:
    """按 (kind, 归一化标题) 去重，保留文本更长的那条。

    标题归一化去掉了标点与大小写——同一个故事被两家媒体转述时，
    标题往往只差一个冒号。
    """

    best: dict[tuple[str, str], Item] = {}
    order: list[tuple[str, str]] = []
    for item in items:
        key = (item.kind, _title_key(item.title))
        current = best.get(key)
        if current is None:
            best[key] = item
            order.append(key)
        elif len(item.text) > len(current.text):
            best[key] = item
    return [best[key] for key in order]
