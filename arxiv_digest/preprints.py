"""把期刊论文映射回 arXiv 预印本：补摘要、取全文。

Crossref 给的 PDF 链接通常被出版商挡住（实测 IOP 的 ``/pdf`` 返回 HTML、
doi.org 跟随跳转 403），而绝大多数天文期刊论文都有 arXiv 版本。这里只做
一件保守的事：用标题在 arXiv 上找同一篇，验证标题相似度、第一作者姓氏与
年份，命中才返回——宁可少一篇全文，也不把别人论文的 PDF 当成这篇的。

缓存写在 ``<cache_dir>/preprints/<sha256>.json``：命中留 180 天，
未命中留 14 天，避免每次运行都重复问 arXiv。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import ssl
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Paper

LOGGER = logging.getLogger(__name__)

API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
MIN_TITLE_RATIO = 0.90
MAX_YEAR_GAP = 2
MIN_SEARCH_GAP = 3.0
CACHE_NAMESPACE = "preprints"
HIT_TTL_SECONDS = 180 * 86400
MISS_TTL_SECONDS = 14 * 86400
USER_AGENT = "arxiv-astro-digest/preprints"

_last_request_at = 0.0


@dataclass(frozen=True)
class PreprintMatch:
    arxiv_id: str
    title: str
    summary: str
    published: datetime
    authors: tuple[str, ...] = ()

    @property
    def pdf_url(self) -> str:
        return f"https://arxiv.org/pdf/{self.arxiv_id}"

    @property
    def abs_url(self) -> str:
        return f"https://arxiv.org/abs/{self.arxiv_id}"


def _ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _title_ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize_title(left), _normalize_title(right)).ratio()


def _surname(name: str) -> str:
    text = re.sub(r"[^a-z\s\-]", " ", unicodedata.normalize("NFKD", name or "").lower())
    parts = [part for part in re.split(r"[\s\-]+", text) if part]
    return parts[-1] if parts else ""


def _year(value: datetime | None) -> int | None:
    return value.year if isinstance(value, datetime) else None


def _clean(value: str) -> str:
    import html as _html

    return re.sub(r"\s+", " ", _html.unescape(value or "")).strip()


def _search_title(paper: Paper) -> str:
    title = re.sub(r"[^A-Za-z0-9 \-]", " ", paper.title or "")
    return re.sub(r"\s+", " ", title).strip()[:200]


def _query_body(query: str, *, max_results: int, timeout: float) -> str:
    params = urlencode(
        {
            "search_query": query,
            "start": 0,
            "max_results": max(1, max_results),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
    )
    request = Request(f"{API_URL}?{params}", headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        return response.read().decode("utf-8", "replace")


def _parse_entries(body: str) -> list[PreprintMatch]:
    matches: list[PreprintMatch] = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return matches
    for entry in root.findall("atom:entry", namespaces=ATOM_NS):
        raw_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
        arxiv_id = re.sub(r"^https?://[^/]+/abs/", "", raw_id)
        arxiv_id = re.sub(r"v\d+$", "", arxiv_id)
        if not arxiv_id:
            continue
        raw_date = entry.findtext("atom:published", default="", namespaces=ATOM_NS)
        try:
            published = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except ValueError:
            published = datetime(1970, 1, 1, tzinfo=timezone.utc)
        matches.append(
            PreprintMatch(
                arxiv_id=arxiv_id,
                title=_clean(entry.findtext("atom:title", default="", namespaces=ATOM_NS)),
                summary=_clean(entry.findtext("atom:summary", default="", namespaces=ATOM_NS)),
                published=published,
                authors=tuple(
                    _clean(author.findtext("atom:name", default="", namespaces=ATOM_NS))
                    for author in entry.findall("atom:author", namespaces=ATOM_NS)
                ),
            )
        )
    return matches


def _authors_confirmed(paper: Paper, candidate: PreprintMatch) -> bool:
    paper_surnames = {_surname(name) for name in paper.authors if name}
    candidate_surnames = {_surname(name) for name in candidate.authors if name}
    paper_surnames.discard("")
    candidate_surnames.discard("")
    if not paper_surnames or not candidate_surnames:
        return True  # 缺作者信息时不拿它当否决条件
    first = _surname(paper.authors[0]) if paper.authors else ""
    if first and first in candidate_surnames:
        return True
    return len(paper_surnames & candidate_surnames) >= 2


def _years_close(paper: Paper, candidate: PreprintMatch) -> bool:
    left, right = _year(paper.published), _year(candidate.published)
    if left is None or right is None:
        return True
    return abs(left - right) <= MAX_YEAR_GAP


def match_candidates(paper: Paper, candidates: list[PreprintMatch]) -> PreprintMatch | None:
    """在候选里挑同一篇；标题、作者、年份三项都要过，宁缺毋滥。"""
    best: PreprintMatch | None = None
    best_ratio = 0.0
    for candidate in candidates:
        ratio = _title_ratio(paper.title, candidate.title)
        if ratio < MIN_TITLE_RATIO:
            continue
        if not _authors_confirmed(paper, candidate) or not _years_close(paper, candidate):
            continue
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
    return best


def find_preprint(
    paper: Paper,
    *,
    timeout: float = 20.0,
    max_results: int = 5,
    throttle: bool = True,
) -> PreprintMatch | None:
    title = _search_title(paper)
    if len(title) < 12:
        return None
    queries = [f'ti:"{title}"', f'all:"{title}"']
    for index, query in enumerate(queries):
        if index and not throttle:
            pass
        if throttle:
            _throttle()
        try:
            body = _query_body(query, max_results=max_results, timeout=timeout)
        except (HTTPError, URLError, TimeoutError, ssl.SSLError, OSError) as exc:
            LOGGER.warning("arXiv 预印本查询失败（%s）：%s", type(exc).__name__, paper.id)
            return None
        candidates = _parse_entries(body)
        match = match_candidates(paper, candidates)
        if match is not None:
            return match
        if candidates:
            # 有结果但都对不上，再换 all: 只会更宽，直接停。
            break
    return None


def _throttle() -> None:
    global _last_request_at
    wait = MIN_SEARCH_GAP - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _cache_key(paper: Paper) -> str:
    token = (paper.doi or _normalize_title(paper.title)).strip().lower()
    return hashlib.sha256(f"v1:{token}".encode("utf-8")).hexdigest()


def _cache_path(cache_dir: str | Path, paper: Paper) -> Path:
    return Path(cache_dir) / CACHE_NAMESPACE / f"{_cache_key(paper)}.json"


def _read_cache(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    checked_at = payload.get("checked_at")
    if status not in ("hit", "miss") or not isinstance(checked_at, (int, float)):
        return None
    ttl = HIT_TTL_SECONDS if status == "hit" else MISS_TTL_SECONDS
    if time.time() - checked_at > ttl:
        return None
    if status == "hit" and not isinstance(payload.get("match"), dict):
        return None
    return payload


def _write_cache(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        LOGGER.info("预印本缓存写入失败，忽略：%s", path)


def _match_from_payload(raw: dict) -> PreprintMatch | None:
    try:
        published = datetime.fromisoformat(str(raw["published"]).replace("Z", "+00:00"))
        return PreprintMatch(
            arxiv_id=str(raw["arxiv_id"]),
            title=str(raw.get("title", "")),
            summary=str(raw.get("summary", "")),
            published=published,
            authors=tuple(str(name) for name in raw.get("authors") or ()),
        )
    except (KeyError, TypeError, ValueError):
        return None


def resolve_preprint(
    paper: Paper,
    *,
    cache_dir: str | Path = "",
    timeout: float = 20.0,
    max_results: int = 5,
    throttle: bool = True,
) -> PreprintMatch | None:
    """查 arXiv 上的同一篇论文；带正/负缓存。"""
    path = _cache_path(cache_dir, paper) if cache_dir else None
    if path is not None:
        cached = _read_cache(path)
        if cached is not None:
            if cached.get("status") == "miss":
                return None
            match = _match_from_payload(cached.get("match") or {})
            if match is not None:
                return match
    match = find_preprint(paper, timeout=timeout, max_results=max_results, throttle=throttle)
    if path is not None:
        _write_cache(
            path,
            {
                "status": "hit" if match else "miss",
                "checked_at": time.time(),
                "match": (
                    {
                        "arxiv_id": match.arxiv_id,
                        "title": match.title,
                        "summary": match.summary,
                        "published": match.published.isoformat(),
                        "authors": list(match.authors),
                    }
                    if match
                    else None
                ),
            },
        )
    return match


def preprint_pdf_url(
    paper: Paper,
    *,
    cache_dir: str | Path = "",
    timeout: float = 20.0,
    max_results: int = 5,
    throttle: bool = True,
) -> str | None:
    match = resolve_preprint(
        paper, cache_dir=cache_dir, timeout=timeout, max_results=max_results, throttle=throttle
    )
    return match.pdf_url if match else None


def enrich_summary(
    paper: Paper,
    *,
    cache_dir: str | Path = "",
    timeout: float = 20.0,
    max_results: int = 5,
    throttle: bool = True,
) -> Paper:
    """期刊论文缺摘要时用 arXiv 版本补上；来源、DOI、期刊名保持不变。"""
    if (paper.summary or "").strip():
        return paper
    match = resolve_preprint(
        paper, cache_dir=cache_dir, timeout=timeout, max_results=max_results, throttle=throttle
    )
    if match is None or not match.summary:
        return paper
    LOGGER.info("期刊论文 %s 用 arXiv 预印本补摘要：%s", paper.venue or paper.id, match.arxiv_id)
    return replace(paper, summary=match.summary)
