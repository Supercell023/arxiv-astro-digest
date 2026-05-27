from __future__ import annotations

import re

from .models import Paper, ZoteroPreference
from .zotero_client import zotero_similarity_score


def _normalize_for_matching(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[-/]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _keyword_score(paper: Paper, keywords: list[str]) -> tuple[float, list[str]]:
    if not keywords:
        return 0.0, []

    haystack = _normalize_for_matching(f"{paper.title} {paper.summary}")
    score = 0.0
    reasons: list[str] = []
    for keyword in keywords:
        kw_normalized = _normalize_for_matching(keyword)
        if not kw_normalized:
            continue
        pattern = rf"\b{re.escape(kw_normalized)}\b"
        count = len(re.findall(pattern, haystack))
        if count:
            score += min(3.0, 0.8 * count)
            reasons.append(f"keyword: {keyword}")
    return score, reasons


def score_to_rating(score: float) -> int:
    """Map the internal recommender score to a human-friendly 1-5 star rating."""
    if score >= 5.0:
        return 5
    if score >= 3.0:
        return 4
    if score >= 1.2:
        return 3
    if score >= 0.4:
        return 2
    return 1


def rank_papers(papers: list[Paper], keywords: list[str], preferences: list[ZoteroPreference], limit: int) -> list[Paper]:
    ranked: list[Paper] = []
    for paper in papers:
        keyword_score, keyword_reasons = _keyword_score(paper, keywords)
        zotero_score, zotero_reasons = zotero_similarity_score(paper.title, paper.summary, preferences)
        category_bonus = 0.2 * len(paper.categories)
        score = keyword_score + zotero_score + category_bonus
        ranked.append(
            Paper(
                **{
                    **paper.__dict__,
                    "score": score,
                    "rating": score_to_rating(score),
                    "score_reasons": keyword_reasons[:3] + zotero_reasons,
                }
            )
        )
    return sorted(ranked, key=lambda item: (item.score, item.published), reverse=True)[:limit]


def filter_by_min_rating(papers: list[Paper], min_rating: int) -> list[Paper]:
    min_rating = max(1, min(5, min_rating))
    return [paper for paper in papers if paper.rating >= min_rating]
