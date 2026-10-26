"""期刊源：arXiv 之外，把 ApJ / MNRAS / Nature Astronomy 也纳入每日简报。

为什么走 Crossref 而不是各家 RSS：

- 一个接口覆盖全部期刊，ISSN 定刊，`from-pub-date` 定窗口，不用为每本刊写一个解析器；
- 返回里有 DOI、标题、作者、摘要（JATS XML），足够填 `Paper`；
- 不需要 API key，带 `mailto` 就进 polite pool。

代价是摘要质量参差：Nature Astronomy 的部分条目没有 abstract，ApJ/MNRAS 的是
JATS 富文本。所以这里**容忍空摘要**——没有摘要的条目照样进候选，只是拿不到关键词分。
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from collections.abc import Callable

from . import __version__
from .models import Paper

LOGGER = logging.getLogger(__name__)

CROSSREF_URL = "https://api.crossref.org/works"
DEFAULT_TIMEOUT = 30.0
DEFAULT_ROWS = 40
DEFAULT_MAILTO = "arxiv-digest@example.com"

_JATS_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Journal:
    """一本刊：ISSN 用来查 Crossref，pack 用来给期刊论文指定领域包。"""

    id: str
    name: str
    issn: tuple[str, ...]
    pack: str = ""
    enabled: bool = True

    @property
    def display(self) -> str:
        return self.id


def _as_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def load_journals(path: str | Path = "config/journals.yaml") -> tuple[Journal, ...]:
    """读 `config/journals.yaml`；文件缺失或损坏时返回空（期刊源整体关闭）。"""

    target = Path(path)
    if not target.exists():
        return ()
    try:
        import yaml
    except ImportError:
        LOGGER.warning("pyyaml 未安装，无法读取期刊源配置。")
        return ()

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("读取期刊源配置失败（%s）：%s", target, exc)
        return ()
    if not isinstance(raw, dict):
        return ()

    journals: list[Journal] = []
    for entry in raw.get("journals") or []:
        if not isinstance(entry, dict):
            continue
        journal_id = str(entry.get("id") or "").strip()
        issn = _as_tuple(entry.get("issn"))
        if not journal_id or not issn:
            continue
        journals.append(
            Journal(
                id=journal_id,
                name=str(entry.get("name") or journal_id).strip(),
                issn=issn,
                pack=str(entry.get("pack") or "").strip(),
                enabled=bool(entry.get("enabled", True)),
            )
        )
    return tuple(journals)


def clean_abstract(raw: object) -> str:
    """把 Crossref 的 JATS 摘要压成纯文本。"""

    text = str(raw or "")
    if not text:
        return ""
    text = re.sub(r"<jats:title>\s*Abstract\s*</jats:title>", " ", text, flags=re.IGNORECASE)
    text = _JATS_TAG.sub(" ", text)
    text = html.unescape(text)
    return _WS.sub(" ", text).strip()


def _parse_published(item: dict[str, Any]) -> datetime | None:
    for key in ("published", "published-print", "published-online", "issued", "created"):
        block = item.get(key)
        if not isinstance(block, dict):
            continue
        parts = block.get("date-parts")
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
            continue
        numbers = [int(value) for value in parts[0] if isinstance(value, (int, float))]
        if not numbers:
            continue
        year = numbers[0]
        month = numbers[1] if len(numbers) > 1 else 1
        day = numbers[2] if len(numbers) > 2 else 1
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _authors(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        given = str(author.get("given") or "").strip()
        family = str(author.get("family") or "").strip()
        name = " ".join(part for part in (given, family) if part)
        if not name:
            name = str(author.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _doi_to_id(doi: str) -> str:
    return f"doi:{doi.strip().lower()}"


def _item_to_paper(item: dict[str, Any], journal: Journal) -> Paper | None:
    if not isinstance(item, dict):
        return None
    doi = str(item.get("DOI") or "").strip().lower()
    if not doi:
        return None
    titles = item.get("title") or []
    title = ""
    if isinstance(titles, list) and titles:
        title = clean_abstract(titles[0])
    if not title:
        return None
    if item.get("type") and str(item.get("type")) != "journal-article":
        return None

    published = _parse_published(item)
    if published is None:
        return None

    summary = clean_abstract(item.get("abstract"))
    link = f"https://doi.org/{doi}"
    return Paper(
        id=_doi_to_id(doi),
        title=title,
        authors=_authors(item),
        summary=summary,
        published=published,
        updated=published,
        categories=[],
        pdf_url=link,
        abs_url=link,
        venue=journal.id,
        doi=doi,
        pack_hint=journal.pack,
    )


def _fetch_crossref(url: str, *, timeout: float, mailto: str) -> dict[str, Any]:
    headers = {
        "User-Agent": f"arxiv-astro-digest/{__version__} (mailto:{mailto})",
        "Accept": "application/json",
    }
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _journal_url(journal: Journal, *, since: str, rows: int, mailto: str) -> str:
    issn = journal.issn[0]
    query = urllib.parse.urlencode(
        {
            "filter": f"issn:{issn},from-pub-date:{since},type:journal-article",
            "select": "DOI,title,abstract,published,published-print,published-online,issued,author,type",
            "rows": rows,
            "sort": "published",
            "order": "desc",
            "mailto": mailto,
        }
    )
    return f"{CROSSREF_URL}?{query}"


def fetch_journal_papers(
    journals: Sequence[Journal],
    *,
    lookback_days: int = 2,
    rows: int = DEFAULT_ROWS,
    max_per_journal: int = 6,
    timeout: float = DEFAULT_TIMEOUT,
    mailto: str = DEFAULT_MAILTO,
    predicate: Callable[[Paper], bool] | None = None,
    preprocess: Callable[[Paper], Paper] | None = None,
) -> list[Paper]:
    """按窗口抓每本刊的最新论文；单刊失败只 warning。

    ``preprocess`` 在 ``predicate`` 之前逐篇调用，用来补缺失摘要（Nature
    Astronomy 在 Crossref 常常只有标题）；补完再看主题闸门，才不会把
    "只有标题"的稿子误判成不相关。
    """

    since_date = (datetime.now(timezone.utc) - timedelta(days=max(1, lookback_days))).date()
    since = since_date.isoformat()

    collected: dict[str, Paper] = {}
    for journal in journals:
        if not journal.enabled:
            continue
        url = _journal_url(journal, since=since, rows=rows, mailto=mailto)
        try:
            payload = _fetch_crossref(url, timeout=timeout, mailto=mailto)
        except urllib.error.HTTPError as exc:
            LOGGER.warning("Crossref 返回 HTTP %s（%s），跳过。", exc.code, journal.id)
            continue
        except Exception as exc:  # noqa: BLE001 - 抓不到不该让整封信失败
            LOGGER.warning("抓取 %s 失败：%s", journal.id, exc)
            continue

        message = payload.get("message") if isinstance(payload, dict) else None
        items = message.get("items") if isinstance(message, dict) else None
        if not isinstance(items, list):
            continue

        accepted = 0
        for item in items:
            if accepted >= max_per_journal:
                break
            paper = _item_to_paper(item, journal)
            if paper is None:
                continue
            if preprocess is not None:
                paper = preprocess(paper)
            if predicate is not None and not predicate(paper):
                continue
            collected.setdefault(paper.id, paper)
            accepted += 1

    return sorted(collected.values(), key=lambda paper: paper.published, reverse=True)


def _title_key(title: str) -> str:
    return " ".join(re.findall(r"\w+", str(title or "").casefold()))


def merge_with_arxiv(arxiv_papers: Sequence[Paper], journal_papers: Sequence[Paper]) -> list[Paper]:
    """合并两个来源；同一篇（标题相同）只留一份。

    arXiv 版优先：它有 abs/pdf 链接、有真实的 arXiv 分类、后面抓全文也走它。
    期刊版的价值在 DOI 与期刊名，所以遇到重复时把 venue/doi 补给 arXiv 那条。
    """

    merged: list[Paper] = []
    by_title: dict[str, int] = {}
    by_doi: dict[str, int] = {}
    for paper in arxiv_papers:
        merged.append(paper)
        by_title.setdefault(_title_key(paper.title), len(merged) - 1)
        if paper.doi:
            by_doi[paper.doi.lower()] = len(merged) - 1

    for paper in journal_papers:
        key = _title_key(paper.title)
        index = by_doi.get(paper.doi.lower()) if paper.doi else None
        if index is None:
            index = by_title.get(key)
        if index is None:
            by_title[key] = len(merged)
            merged.append(paper)
            if paper.doi:
                by_doi[paper.doi.lower()] = len(merged) - 1
            continue
        existing = merged[index]
        if existing.venue or not (paper.venue or paper.doi):
            continue
        merged[index] = Paper(
            **{
                **existing.__dict__,
                "venue": existing.venue or paper.venue,
                "doi": existing.doi or paper.doi,
                "pack_hint": existing.pack_hint or paper.pack_hint,
            }
        )
        if paper.doi:
            by_doi[paper.doi.lower()] = index
    return merged
