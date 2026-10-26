from __future__ import annotations

import re
from typing import Sequence

from .models import Paper, ScoreBreakdown, ZoteroItem, ZoteroPreference
from .zotero_client import LIBRARY_PENALTY, library_match, zotero_similarity_score

# 挂多少分类就给多少分，是纯失真：一篇 cross-list 六个类的论文能白拿 1.2 分。
# 分类只该证明"它落在你订的窗口里"，两封顶。
MAX_CATEGORY_COUNT = 2
CATEGORY_BONUS = 0.2

# Zotero 相似度是词袋，单项容易刷高；夹住上下界，避免它单独决定排序
ZOTERO_MIN = -2.5
ZOTERO_MAX = 3.0


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


def rank_papers(
    papers: list[Paper],
    keywords: list[str],
    preferences: list[ZoteroPreference],
    limit: int,
    *,
    library: Sequence[ZoteroItem] = (),
) -> list[Paper]:
    ranked: list[Paper] = []
    for paper in papers:
        keyword_score, keyword_reasons = _keyword_score(paper, keywords)
        zotero_raw, zotero_reasons = zotero_similarity_score(
            paper.title, paper.summary, preferences
        )
        zotero_score = max(ZOTERO_MIN, min(ZOTERO_MAX, zotero_raw))
        category_bonus = CATEGORY_BONUS * min(len(paper.categories), MAX_CATEGORY_COUNT)

        matched = library_match(paper, library)
        library_score = LIBRARY_PENALTY if matched is not None else 0.0

        breakdown = ScoreBreakdown(
            keyword=keyword_score,
            zotero=zotero_score,
            category=category_bonus,
            library=library_score,
        )
        score = breakdown.total

        reasons = keyword_reasons[:3] + zotero_reasons
        if matched is not None:
            reasons.append(f"已在 Zotero 库：{matched.title[:30]}")

        ranked.append(
            Paper(
                **{
                    **paper.__dict__,
                    "score": score,
                    "rating": score_to_rating(score),
                    "score_reasons": reasons,
                    "in_library": matched is not None,
                    "breakdown": breakdown,
                }
            )
        )
    return sorted(ranked, key=lambda item: (item.score, item.published), reverse=True)[:limit]


def filter_by_min_rating(papers: list[Paper], min_rating: int) -> list[Paper]:
    min_rating = max(1, min(5, min_rating))
    return [paper for paper in papers if paper.rating >= min_rating]
