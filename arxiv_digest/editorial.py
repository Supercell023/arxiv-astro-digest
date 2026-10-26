"""Deterministic edition, research relevance and news selection policies."""
from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from .config import Config
from .models import Paper
from .packs import Pack
from .profile import Profile
from .recommender import _normalize_for_matching, score_to_rating
from .sources.base import Item, _title_key

NEWS_DOMAINS = {"astronomy": "天文学", "physics": "物理学", "chemistry": "化学", "history": "历史与考古"}


@dataclass(frozen=True)
class Edition:
    kind: str
    start: date
    end: date  # exclusive, in the configured timezone

    @property
    def key(self) -> str:
        return f"weekly:{self.start}:{self.end}" if self.kind == "weekly" else ""

    @property
    def period_label(self) -> str:
        return f"{self.start} 至 {self.end - timedelta(days=1)}"


def edition_for(config: Config, today: date) -> Edition:
    weekly = config.digest_edition == "weekly" or (
        config.digest_edition == "auto" and config.weekly_enabled and today.weekday() == config.weekly_weekday)
    return Edition("weekly" if weekly else "daily", today - timedelta(days=7 if weekly else config.lookback_days), today)


def edition_config(config: Config, edition: Edition) -> Config:
    if edition.kind != "weekly":
        return config
    return replace(config, max_papers=config.weekly_max_papers, deep_papers=config.weekly_deep_papers,
                   brief_papers=config.weekly_max_papers - config.weekly_deep_papers,
                   max_llm_papers=max(config.max_llm_papers, config.weekly_max_papers), lookback_days=7,
                   journals_lookback_days=7, journals_rows=max(config.journals_rows, 150),
                   journals_max_per=max(config.journals_max_per, 30))


def in_period(paper: Paper, edition: Edition, timezone_name: str) -> bool:
    stamp = paper.published
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return edition.start <= stamp.astimezone(ZoneInfo(timezone_name)).date() < edition.end


def relevant_papers(papers: Sequence[Paper], keywords: Sequence[str], packs: Sequence[Pack],
                    profile: Profile, *, min_signals: int = 1, strict: bool = False) -> list[Paper]:
    """Category membership alone is insufficient. Repeated synonyms count once.

    Explicit user terms / primary probes are strong signals; otherwise weekly picks
    require multiple distinct topic phrases or a phrase in the title. Ranking is
    supplied by the recommender and remains deterministic and explainable.
    """
    strong = {_normalize_for_matching(term) for term in (*keywords, *profile.probes_primary) if term.strip()}
    topics = {_normalize_for_matching(term) for pack in packs if not pack.is_generic
              for term in (pack.journal_terms or pack.title_keywords) if term.strip()}
    terms = strong | topics
    if not terms:
        return list(papers)  # custom broad subscriptions with no interest vocabulary

    def matched(text: str) -> list[str]:
        normalized = _normalize_for_matching(text)
        hits = [term for term in terms if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized)]
        # e.g. galaxy cluster / galaxy clusters, weak lensing / lensing are not independent evidence.
        return sorted((term for term in hits if not any(term != other and term in other for other in hits)),
                      key=lambda term: (-len(term), term))

    selected = []
    for paper in papers:
        hits = matched(f"{paper.title} {paper.summary}")
        title_hits = matched(paper.title)
        strong_hit = bool(set(hits) & strong)
        enough = len(hits) >= min_signals or strong_hit or (strict and bool(title_hits))
        if not hits or not enough:
            continue
        reasons = list(paper.score_reasons) + [f"兴趣匹配：{', '.join(hits[:4])}"]
        interest_score = 0.8 * len(hits) + 1.2 * len(title_hits) + (1.2 if strong_hit else 0)
        breakdown = replace(paper.breakdown, keyword=paper.breakdown.keyword + interest_score)
        selected.append(replace(paper, score_reasons=reasons, breakdown=breakdown,
                                score=breakdown.total, rating=score_to_rating(breakdown.total)))
    return sorted(selected, key=lambda item: (item.score, item.published), reverse=True)


def news_domain(item: Item) -> str:
    domain = item.meta.get("domain", "")
    if domain in NEWS_DOMAINS:
        return domain
    tags = set(item.tags)
    return next((name for name in NEWS_DOMAINS if name in tags), "astronomy")


def select_news(items: Sequence[Item], *, today: date, timezone_name: str, target: int = 30,
                maximum: int = 36, astronomy_percent: int = 50, max_age_days: int = 7) -> list[Item]:
    """Fresh, unsent candidates; soft quotas backfill shortages, never fabricate items.

    Normally aim for target. Abundant inventories (>2*target) can grow to maximum.
    Other fields rotate so one fast publisher cannot take the entire non-astro half.
    """
    zone = ZoneInfo(timezone_name)
    dated = []
    for item in items:
        try:
            stamp = datetime.fromisoformat(item.published.replace("Z", "+00:00"))
            stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
            if not today - timedelta(days=max_age_days - 1) <= stamp.astimezone(zone).date() <= today:
                continue
        except (ValueError, TypeError, AttributeError, OverflowError):
            continue
        dated.append((stamp, item))
    dated.sort(key=lambda pair: (pair[0], pair[1].id), reverse=True)
    # Link duplicate identities transitively: A shares B's URL and C's title.
    # Dropping A too early would incorrectly leave both B and C in the newsletter.
    parents = list(range(len(dated)))
    keys = {}

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    valid_indices = []
    for index, (_, item) in enumerate(dated):
        try:
            parts = urlsplit(item.url)
            query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                     if not key.lower().startswith("utm_") and key.lower() not in {"gclid", "fbclid"}]
            url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"),
                              urlencode(sorted(query)), ""))
        except ValueError:
            continue
        valid_indices.append(index)
        for kind, value in (("id", item.id), ("url", url), ("title", _title_key(item.title))):
            if not value:
                continue
            key = (kind, value)
            if key in keys:
                parents[find(index)] = find(keys[key])
            keys[key] = index
    groups = {}
    for index in valid_indices:
        root = find(index)
        candidate = dated[index]
        current = groups.get(root)
        if current is None or (candidate[0], len(candidate[1].text)) > (current[0], len(current[1].text)):
            groups[root] = candidate
    fresh = sorted(groups.values(), key=lambda pair: (pair[0], pair[1].id), reverse=True)
    pools = {domain: deque(item for _, item in fresh if news_domain(item) == domain) for domain in NEWS_DOMAINS}
    others = []
    while any(pools[name] for name in NEWS_DOMAINS if name != "astronomy"):
        for name in ("physics", "chemistry", "history"):
            if pools[name]:
                others.append(pools[name].popleft())
    astro = list(pools["astronomy"])
    budget = min(len(fresh), maximum, max(target, (len(fresh) + 1) // 2))
    astro_quota = (budget * astronomy_percent + 50) // 100
    chosen = astro[:astro_quota] + others[:budget - astro_quota]
    used = {id(item) for item in chosen}
    # Fill whichever side is short; preference is still newest first.
    remainder = [item for _, item in fresh if id(item) not in used]
    chosen.extend(remainder[:budget - len(chosen)])
    # Keep the main field together, followed by clearly labelled broader interests.
    order = {id(item): index for index, (_, item) in enumerate(fresh)}
    return sorted(chosen, key=lambda item: (list(NEWS_DOMAINS).index(news_domain(item)), order[id(item)]))


def news_counts(items: Sequence[Item]) -> dict[str, int]:
    counts = Counter(news_domain(item) for item in items)
    return {NEWS_DOMAINS[name]: counts[name] for name in NEWS_DOMAINS if counts[name]}
