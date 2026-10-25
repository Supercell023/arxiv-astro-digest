"""反馈闭环：把"这篇有用/一般/无关"变成下一周的排序偏好。

邮件里点按钮只是把参数带到一个 URL——真正的闭环靠一个能收住这些点击的地方：
Google Form、Cloudflare Worker、或者你自己的一个小脚本（`scripts/feedback.py`）。
无论哪种，最后都落成同一种文件：

    {
      "version": 1,
      "votes": {"2610.31660v1": "up"},
      "history": {"2610.31660v1": {"categories": ["astro-ph.CO"], "authors": ["Vitorelli"]}}
    }

排序时只看"投过票的论文长什么样"：分类和作者给出小权重，命中就加分。
故意不做"关键词自动改写"——那是把人的偏好悄悄改掉，比不学习更糟。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .models import Paper

LOGGER = logging.getLogger(__name__)

VOTE_UP = "up"
VOTE_MEH = "meh"
VOTE_DOWN = "down"
_VOTE_SCORE = {VOTE_UP: 1.0, VOTE_MEH: 0.0, VOTE_DOWN: -1.0}

CATEGORY_WEIGHT = 0.6
AUTHOR_WEIGHT = 0.35
MAX_BOOST = 1.8


@dataclass
class FeedbackStore:
    version: int = 1
    votes: dict[str, str] = field(default_factory=dict)
    history: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.votes

    def to_dict(self) -> dict[str, object]:
        return {"version": self.version, "votes": self.votes, "history": self.history}


def load_feedback(path: str | Path) -> FeedbackStore:
    """读反馈文件；缺失或损坏都返回空 store，不抛异常。"""

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return FeedbackStore()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("读取反馈文件失败（%s）：%s", path, exc)
        return FeedbackStore()
    if not isinstance(raw, dict):
        return FeedbackStore()

    votes = {
        str(key): str(value).lower()
        for key, value in (raw.get("votes") or {}).items()
        if str(value).lower() in _VOTE_SCORE
    }
    history: dict[str, dict[str, list[str]]] = {}
    for key, value in (raw.get("history") or {}).items():
        if not isinstance(value, dict):
            continue
        history[str(key)] = {
            "categories": [str(item) for item in (value.get("categories") or [])],
            "authors": [str(item) for item in (value.get("authors") or [])],
        }
    return FeedbackStore(version=int(raw.get("version") or 1), votes=votes, history=history)


def save_feedback(path: str | Path, store: FeedbackStore) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(store.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def record_vote(store: FeedbackStore, paper: Paper, vote: str) -> bool:
    """记一票；同一篇重复投票以最后一次为准。"""

    if vote not in _VOTE_SCORE:
        return False
    store.votes[paper.id] = vote
    store.history[paper.id] = {
        "categories": list(paper.categories),
        "authors": list(paper.authors),
    }
    return True


def _author_surname(name: str) -> str:
    """取姓氏：arXiv 常见 "A. B. Lastname"，也兼容 "Lastname, Given"。"""

    cleaned = name.strip()
    if not cleaned:
        return ""
    if "," in cleaned:
        return cleaned.split(",", 1)[0].strip().lower()
    parts = [item for item in cleaned.split() if item]
    return parts[-1].lower() if parts else ""


def learned_weights(store: FeedbackStore) -> tuple[dict[str, float], dict[str, float]]:
    """把投票折算成分类权重与作者权重（归一化到 [-1, 1]）。"""

    category_raw: dict[str, float] = {}
    author_raw: dict[str, float] = {}
    for item_id, vote in store.votes.items():
        meta = store.history.get(item_id) or {}
        score = _VOTE_SCORE.get(vote, 0.0)
        if not score:
            continue
        for category in meta.get("categories") or []:
            category_raw[category] = category_raw.get(category, 0.0) + score
        for author in meta.get("authors") or []:
            surname = _author_surname(author)
            if surname:
                author_raw[surname] = author_raw.get(surname, 0.0) + score

    def _normalise(raw: Mapping[str, float]) -> dict[str, float]:
        if not raw:
            return {}
        scale = max(abs(value) for value in raw.values()) or 1.0
        return {key: value / scale for key, value in raw.items()}

    return _normalise(category_raw), _normalise(author_raw)


def boost_for(paper: Paper, categories: Mapping[str, float], authors: Mapping[str, float]) -> float:
    score = 0.0
    for category in paper.categories:
        score += CATEGORY_WEIGHT * categories.get(category, 0.0)
    for author in paper.authors:
        score += AUTHOR_WEIGHT * authors.get(_author_surname(author), 0.0)
    return max(-MAX_BOOST, min(MAX_BOOST, score))


def apply_feedback(store: FeedbackStore, papers: list[Paper]) -> list[Paper]:
    """按学到的偏好调整分数；无反馈时原样返回。"""

    if store.is_empty:
        return papers
    categories, authors = learned_weights(store)
    if not categories and not authors:
        return papers
    adjusted: list[Paper] = []
    for paper in papers:
        delta = boost_for(paper, categories, authors)
        if not delta:
            adjusted.append(paper)
            continue
        payload = dict(paper.__dict__)
        payload["score"] = round(float(paper.score) + delta, 3)
        adjusted.append(Paper(**payload))
    adjusted.sort(key=lambda item: (item.score, item.published), reverse=True)
    return adjusted


def format_plain_text(store: FeedbackStore) -> str:
    if store.is_empty:
        return "（还没有反馈记录）"
    categories, authors = learned_weights(store)
    up = sum(1 for vote in store.votes.values() if vote == VOTE_UP)
    down = sum(1 for vote in store.votes.values() if vote == VOTE_DOWN)
    lines = [f"反馈记录：{len(store.votes)} 篇（有用 {up} / 无关 {down}）"]
    if categories:
        top = sorted(categories.items(), key=lambda item: item[1], reverse=True)[:5]
        lines.append("分类偏好：" + "，".join(f"{key} {value:+.2f}" for key, value in top))
    if authors:
        top = sorted(authors.items(), key=lambda item: item[1], reverse=True)[:5]
        lines.append("作者偏好：" + "，".join(f"{key} {value:+.2f}" for key, value in top))
    return "\n".join(lines)
