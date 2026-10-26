"""Zotero 只读接入：把"我已经收了什么"变成三条可用的信息。

这里要区分两件事，混在一起就会把推荐做坏：

1. **相似度**：候选论文和你库里的东西长得像 → 加分。这是原来的词袋相似度。
2. **已收录**：候选论文本身就是库里那篇 → 减分（别再当新论文推给你）。

另外，库里的标签（favorite / important / irrelevant…）是读者手写的偏好，
比任何推测都可靠——有标签的条目按标签打分，没有标签的按 0.5 计入相似度池。
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable, Sequence

from .models import Paper, ZoteroItem, ZoteroPreference

LOGGER = logging.getLogger(__name__)

API_ROOT = "https://api.zotero.org"

POSITIVE_TAGS = {
    "favorite": 3.0,
    "favourite": 3.0,
    "important": 2.5,
    "interested": 2.0,
    "high-interest": 3.0,
    "must-read": 3.0,
    "relevant": 1.5,
}
NEGATIVE_TAGS = {
    "not-interesting": -2.0,
    "irrelevant": -2.0,
    "low-interest": -1.5,
    "skip": -2.0,
}

# 没有标签的条目也参与相似度，但权重低：收藏本身是弱信号
UNTAGGED_WEIGHT = 0.5

# 库内命中的扣分：压过大多数关键词加成，但不至于把"已收藏且高度相关"彻底埋掉
LIBRARY_PENALTY = -3.0

_ARXIV_IN_TEXT = re.compile(r"arXiv[:\s]*(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
_ARXIV_IN_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", re.IGNORECASE)
_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")


def _score_tags(tags: Sequence[str]) -> float:
    score = 0.0
    for tag in tags:
        normalized = str(tag).strip().lower()
        score += POSITIVE_TAGS.get(normalized, 0.0)
        score += NEGATIVE_TAGS.get(normalized, 0.0)
    return score


def _clean_doi(raw: str) -> str:
    doi = str(raw or "").strip().lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    return doi.rstrip(".")


def _arxiv_from_extra(extra: str) -> str:
    match = _ARXIV_IN_TEXT.search(extra or "")
    return match.group(1) if match else ""


def _item_to_entry(raw: dict) -> ZoteroItem | None:
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        return None
    item_type = str(data.get("itemType") or "")
    if item_type in {"attachment", "note", "annotation", "collection", "search"}:
        return None

    title = str(data.get("title") or "").strip()
    if not title:
        return None

    tags = tuple(
        str(tag.get("tag")).strip()
        for tag in (data.get("tags") or [])
        if isinstance(tag, dict) and tag.get("tag")
    )

    extra = str(data.get("extra") or "")
    doi = _clean_doi(data.get("DOI") or "")
    if not doi:
        match = _DOI_IN_TEXT.search(extra)
        if match:
            doi = _clean_doi(match.group(0))

    arxiv_id = ""
    if str(data.get("archive") or "").lower() == "arxiv":
        arxiv_id = str(data.get("archiveID") or "").strip()
    arxiv_id = arxiv_id or _arxiv_from_extra(extra)
    if not arxiv_id:
        match = _ARXIV_IN_URL.search(str(data.get("url") or ""))
        arxiv_id = match.group(1) if match else ""
    arxiv_id = re.sub(r"v\d+$", "", arxiv_id.strip()) if arxiv_id else ""
    # 真实库里 archiveID 常写成 "arXiv:2610.12345"，前缀要去掉才能和论文 id 对上
    arxiv_id = re.sub(r"^arxiv[:\s]*", "", arxiv_id, flags=re.IGNORECASE)

    collections = tuple(str(item) for item in (data.get("collections") or []))

    return ZoteroItem(
        key=str(data.get("key") or raw.get("key") or ""),
        title=title,
        item_type=item_type,
        tags=tags,
        abstract=str(data.get("abstractNote") or "").strip(),
        doi=doi,
        arxiv_id=arxiv_id,
        date_added=str(data.get("dateAdded") or ""),
        collections=collections,
        score=_score_tags(tags),
    )


def _base_url(library_type: str, user_id: str, collection_id: str = "") -> str:
    library = "groups" if str(library_type).strip().lower() == "group" else "users"
    base = f"{API_ROOT}/{library}/{urllib.parse.quote(str(user_id).strip())}"
    if collection_id:
        return f"{base}/collections/{urllib.parse.quote(str(collection_id).strip())}/items"
    return f"{base}/items"


def fetch_zotero_items(
    api_key: str,
    user_id: str,
    library_type: str = "user",
    collection_id: str = "",
    *,
    limit: int = 100,
    timeout: float = 30.0,
    max_pages: int = 3,
) -> list[ZoteroItem]:
    """抓库里最近修改的条目（附件与笔记跳过）。失败只 warning，返回已拿到的部分。"""

    if not api_key or not user_id:
        return []
    query = urllib.parse.urlencode(
        {"format": "json", "limit": 100, "sort": "dateModified", "direction": "desc"}
    )
    url = f"{_base_url(library_type, user_id, collection_id)}?{query}"
    headers = {"Zotero-API-Version": "3", "Zotero-API-Key": api_key}
    items: list[ZoteroItem] = []
    start = 0
    for _ in range(max(1, max_pages)):
        if len(items) >= max(1, limit):
            break
        page_url = f"{url}&start={start}"
        request = urllib.request.Request(page_url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            LOGGER.warning("Zotero 返回 HTTP %s（%s），跳过。", exc.code, page_url)
            break
        except Exception as exc:  # noqa: BLE001 - 抓不到库不应该让整封信失败
            LOGGER.warning("读取 Zotero 库失败：%s", exc)
            break
        if not isinstance(payload, list) or not payload:
            break
        for raw in payload:
            entry = _item_to_entry(raw)
            if entry is not None:
                items.append(entry)
        start += len(payload)
        if len(payload) < 100:
            break
    return items[: max(1, limit)]


def preferences_from_items(items: Iterable[ZoteroItem]) -> list[ZoteroPreference]:
    """把库条目折成相似度池：有标签按标签，没标签给弱权重。"""

    preferences: list[ZoteroPreference] = []
    for item in items:
        weight = item.score if item.score else UNTAGGED_WEIGHT
        preferences.append(
            ZoteroPreference(
                title=item.title,
                tags=list(item.tags),
                abstract=item.abstract,
                score=weight,
            )
        )
    return preferences


def fetch_zotero_preferences(
    api_key: str,
    user_id: str,
    library_type: str = "user",
    collection_id: str = "",
    limit: int = 100,
) -> list[ZoteroPreference]:
    """兼容旧调用：抓库 → 折成相似度池。"""

    items = fetch_zotero_items(
        api_key,
        user_id,
        library_type,
        collection_id,
        limit=limit,
    )
    return preferences_from_items(items)


def _normalize_title(text: str) -> str:
    lowered = str(text or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def library_match(paper: Paper, items: Sequence[ZoteroItem]) -> ZoteroItem | None:
    """这篇论文是不是已经在库里？先比 DOI，再比 arXiv id，最后比标题。"""

    if not items:
        return None
    doi = _clean_doi(paper.doi)
    arxiv_id = ""
    if paper.id and not str(paper.id).startswith("doi:"):
        arxiv_id = re.sub(r"v\d+$", "", str(paper.id).strip())
    paper_title = _normalize_title(paper.title)

    for item in items:
        if doi and item.doi and doi == item.doi:
            return item
        if arxiv_id and item.arxiv_id and arxiv_id == item.arxiv_id:
            return item

    if len(paper_title) < 20:
        return None
    for item in items:
        candidate = _normalize_title(item.title)
        if not candidate:
            continue
        if candidate == paper_title:
            return item
        longer = max(len(candidate), len(paper_title))
        if abs(len(candidate) - len(paper_title)) / longer > 0.08:
            continue
        if difflib.SequenceMatcher(None, candidate, paper_title).ratio() >= 0.93:
            return item
    return None


def _terms(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", (text or "").lower())
    }


def zotero_similarity_score(
    title: str,
    abstract: str,
    preferences: Sequence[ZoteroPreference],
) -> tuple[float, list[str]]:
    """候选与库的相似度：标题命中权重 2 倍，摘要 1 倍。

    只对库一侧归一化（沿用旧行为），但标题权重让"同一个对象"比"同一批词"更值钱。
    """

    if not preferences:
        return 0.0, []
    title_terms = _terms(title)
    abstract_terms = _terms(abstract)
    if not title_terms and not abstract_terms:
        return 0.0, []

    total = 0.0
    reasons: list[str] = []
    for preference in preferences:
        pref_terms = _terms(f"{preference.title} {preference.abstract}")
        if not pref_terms:
            continue
        overlap = 2.0 * len(title_terms & pref_terms) + len(abstract_terms & pref_terms)
        weighted = overlap / max(1, len(pref_terms)) * preference.score
        if abs(weighted) >= 0.05:
            total += weighted
            reasons.append(f"zotero: {preference.title[:30]}")
    return total, reasons[:3]
