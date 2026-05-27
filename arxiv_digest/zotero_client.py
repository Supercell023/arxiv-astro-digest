from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import ZoteroPreference

logger = logging.getLogger(__name__)


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


def _normalize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", text.lower())}


def _score_tags(tags: list[str]) -> float:
    score = 0.0
    for tag in tags:
        key = tag.lower().strip()
        score += POSITIVE_TAGS.get(key, 0.0)
        score += NEGATIVE_TAGS.get(key, 0.0)
    return score


def fetch_zotero_preferences(
    api_key: str | None,
    user_id: str | None,
    library_type: str = "user",
    collection_id: str | None = None,
    limit: int = 100,
) -> list[ZoteroPreference]:
    if not api_key or not user_id:
        return []

    library_path = f"{library_type}s/{user_id}"
    collection_path = f"/collections/{collection_id}" if collection_id else ""
    params = urlencode({"format": "json", "limit": limit, "sort": "dateModified", "direction": "desc"})
    url = f"https://api.zotero.org/{library_path}{collection_path}/items?{params}"
    request = Request(
        url,
        headers={"Zotero-API-Key": api_key, "User-Agent": "daily-arxiv-digest/0.1"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            items = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("Zotero preferences disabled for this run: %s", exc)
        return []

    preferences: list[ZoteroPreference] = []
    for item in items:
        data = item.get("data", {})
        if data.get("itemType") in {"attachment", "note"}:
            continue
        tags = [tag.get("tag", "") for tag in data.get("tags", []) if tag.get("tag")]
        title = data.get("title", "")
        abstract = data.get("abstractNote", "")
        score = _score_tags(tags)
        if score != 0:
            preferences.append(ZoteroPreference(title=title, abstract=abstract, tags=tags, score=score))
    return preferences


def zotero_similarity_score(title: str, abstract: str, preferences: list[ZoteroPreference]) -> tuple[float, list[str]]:
    if not preferences:
        return 0.0, []

    paper_terms = _normalize(f"{title} {abstract}")
    total = 0.0
    reasons: list[str] = []
    for preference in preferences:
        preference_terms = _normalize(f"{preference.title} {preference.abstract}")
        if not preference_terms:
            continue
        overlap = paper_terms & preference_terms
        if not overlap:
            continue
        similarity = len(overlap) / max(len(preference_terms), 1)
        weighted = similarity * preference.score
        if abs(weighted) >= 0.05:
            total += weighted
            reasons.append(f"Zotero match: {preference.title[:70]} ({weighted:+.2f})")

    return total, reasons[:3]
