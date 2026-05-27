from __future__ import annotations

import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, time as datetime_time, timedelta, timezone, tzinfo
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import Paper

logger = logging.getLogger(__name__)


ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def _entry_to_paper(entry: ET.Element) -> Paper:
    arxiv_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS).rsplit("/", 1)[-1]
    links = entry.findall("atom:link", namespaces=ATOM_NS)
    pdf_url = ""
    abs_url = f"https://arxiv.org/abs/{arxiv_id}"
    for link in links:
        href = link.attrib.get("href", "")
        if link.attrib.get("title") == "pdf":
            pdf_url = href
        if link.attrib.get("rel") == "alternate":
            abs_url = href

    categories = [
        category.attrib["term"]
        for category in entry.findall("atom:category", namespaces=ATOM_NS)
        if category.attrib.get("term")
    ]

    return Paper(
        id=arxiv_id,
        title=_clean_text(entry.findtext("atom:title", default="", namespaces=ATOM_NS)),
        authors=[
            _clean_text(author.findtext("atom:name", default="", namespaces=ATOM_NS))
            for author in entry.findall("atom:author", namespaces=ATOM_NS)
        ],
        summary=_clean_text(entry.findtext("atom:summary", default="", namespaces=ATOM_NS)),
        published=_parse_datetime(entry.findtext("atom:published", default="", namespaces=ATOM_NS)),
        updated=_parse_datetime(entry.findtext("atom:updated", default="", namespaces=ATOM_NS)),
        categories=categories,
        pdf_url=pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
        abs_url=abs_url,
    )


def _load_timezone(timezone_name: str) -> tzinfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        if timezone_name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), "Asia/Shanghai")
        if timezone_name == "UTC":
            return timezone.utc
        raise ValueError(
            f"Invalid DIGEST_TIMEZONE {timezone_name!r}. Install tzdata or use Asia/Shanghai/UTC."
        ) from exc


def _previous_local_window(days: int, timezone_name: str) -> tuple[datetime, datetime]:
    if days < 1:
        raise ValueError("LOOKBACK_DAYS must be at least 1.")

    local_timezone = _load_timezone(timezone_name)
    today = datetime.now(local_timezone).date()
    end_local = datetime.combine(today, datetime_time.min, tzinfo=local_timezone)
    start_local = end_local - timedelta(days=days)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _fetch_url(url: str, timeout: int, retries: int) -> str:
    last_error: Exception | None = None
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": "daily-arxiv-digest/0.1"})
            with urlopen(request, timeout=timeout) as response:
                body: str = response.read().decode("utf-8")
                return body
        except HTTPError as exc:
            last_error = exc
            if attempt < attempts - 1:
                retry_after = exc.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else max(10, 2**attempt)
                logger.warning("arXiv API returned HTTP %d; retrying in %d seconds.", exc.code, delay)
                time.sleep(delay)
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                delay = 2**attempt
                logger.warning("arXiv API request failed; retrying in %d seconds: %s", delay, exc)
                time.sleep(delay)
    raise RuntimeError(f"Failed to fetch arXiv API after {attempts} attempt(s): {last_error}") from last_error


def fetch_recent_papers(
    categories: list[str],
    lookback_days: int,
    max_results: int = 100,
    timeout: int = 60,
    retries: int = 3,
    timezone_name: str = "Asia/Shanghai",
) -> list[Paper]:
    if not categories:
        raise ValueError("At least one arXiv category is required.")

    query = " OR ".join(f"cat:{category}" for category in categories)
    params = urlencode(
        {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    body = _fetch_url(f"https://export.arxiv.org/api/query?{params}", timeout=timeout, retries=retries)

    root = ET.fromstring(body)
    start_utc, end_utc = _previous_local_window(lookback_days, timezone_name)
    papers = [_entry_to_paper(entry) for entry in root.findall("atom:entry", namespaces=ATOM_NS)]
    filtered = [paper for paper in papers if start_utc <= paper.published < end_utc]

    seen: set[str] = set()
    unique: list[Paper] = []
    for paper in filtered:
        if paper.id not in seen:
            seen.add(paper.id)
            unique.append(paper)
    return unique
