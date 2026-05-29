from __future__ import annotations

import html
import logging
import random
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


class ArxivRateLimitError(RuntimeError):
    """Raised when arXiv keeps returning HTTP 429 after retries."""


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


def _rate_limit_delay(
    attempt: int,
    retry_after: str | None,
    base_seconds: int,
    max_seconds: int,
    jitter_seconds: int,
) -> float:
    if retry_after and retry_after.isdigit():
        return min(int(retry_after), max_seconds)

    base = max(1, base_seconds)
    cap = max(base, max_seconds)
    jitter = random.uniform(0, max(0, jitter_seconds))
    return min(base * (2**attempt) + jitter, cap)


def _fetch_url(
    url: str,
    timeout: int,
    retries: int,
    rate_limit_backoff_base: int = 60,
    rate_limit_backoff_max: int = 600,
    rate_limit_backoff_jitter: int = 30,
) -> str:
    last_error: Exception | None = None
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            request = Request(
                url,
                headers={"User-Agent": "daily-arxiv-digest/0.1"},
            )
            with urlopen(request, timeout=timeout) as response:
                body: str = response.read().decode("utf-8")
                if attempt > 0:
                    logger.info("arXiv API request succeeded after %d retry(s).", attempt)
                return body
        except HTTPError as exc:
            last_error = exc
            if attempt < attempts - 1:
                retry_after = exc.headers.get("Retry-After")
                if exc.code == 429:
                    delay = _rate_limit_delay(
                        attempt,
                        retry_after,
                        rate_limit_backoff_base,
                        rate_limit_backoff_max,
                        rate_limit_backoff_jitter,
                    )
                else:
                    delay = max(10, 2**attempt)
                logger.warning(
                    "arXiv API returned HTTP %d; retry %d/%d in %d seconds.",
                    exc.code, attempt + 1, attempts - 1, int(delay),
                )
                time.sleep(delay)
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                delay = 2**attempt + random.uniform(0, 3)
                logger.warning("arXiv API request failed: %s; retry %d/%d in %d seconds.", exc, attempt + 1, attempts - 1, int(delay))
                time.sleep(delay)
    if isinstance(last_error, HTTPError) and last_error.code == 429:
        raise ArxivRateLimitError(
            f"arXiv API rate limit persisted after {attempts} attempt(s): {last_error}"
        ) from last_error
    raise RuntimeError(f"Failed to fetch arXiv API after {attempts} attempt(s): {last_error}") from last_error


def fetch_recent_papers(
    categories: list[str],
    lookback_days: int,
    max_results: int = 100,
    page_size: int = 25,
    max_pages: int = 4,
    page_delay_seconds: int = 3,
    timeout: int = 60,
    retries: int = 3,
    timezone_name: str = "Asia/Shanghai",
    rate_limit_backoff_base: int = 60,
    rate_limit_backoff_max: int = 600,
    rate_limit_backoff_jitter: int = 30,
) -> list[Paper]:
    if not categories:
        raise ValueError("At least one arXiv category is required.")

    query = " OR ".join(f"cat:{category}" for category in categories)
    start_utc, end_utc = _previous_local_window(lookback_days, timezone_name)
    target_results = max(1, max_results)
    per_page = max(1, min(page_size, target_results))
    pages = max(1, max_pages)

    seen: set[str] = set()
    unique: list[Paper] = []
    for page in range(pages):
        if len(unique) >= target_results:
            break

        start = page * per_page
        remaining = target_results - len(unique)
        current_page_size = min(per_page, remaining)
        params = urlencode(
            {
                "search_query": query,
                "start": start,
                "max_results": current_page_size,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
        )
        logger.info(
            "Fetching arXiv page %d/%d: start=%d, max_results=%d.",
            page + 1,
            pages,
            start,
            current_page_size,
        )
        body = _fetch_url(
            f"https://export.arxiv.org/api/query?{params}",
            timeout=timeout,
            retries=retries,
            rate_limit_backoff_base=rate_limit_backoff_base,
            rate_limit_backoff_max=rate_limit_backoff_max,
            rate_limit_backoff_jitter=rate_limit_backoff_jitter,
        )

        root = ET.fromstring(body)
        papers = [_entry_to_paper(entry) for entry in root.findall("atom:entry", namespaces=ATOM_NS)]
        if not papers:
            break

        older_than_window = True
        for paper in papers:
            if paper.published >= start_utc:
                older_than_window = False
            if not (start_utc <= paper.published < end_utc):
                continue
            if paper.id in seen:
                continue
            seen.add(paper.id)
            unique.append(paper)
            if len(unique) >= target_results:
                break

        if older_than_window:
            logger.info("Stopping arXiv pagination because this page is older than the target date window.")
            break
        if page < pages - 1 and len(unique) < target_results:
            time.sleep(max(0, page_delay_seconds))
    return unique
