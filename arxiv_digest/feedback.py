"""反馈闭环 v2：把"这篇跟我有关吗 / 读着有用吗"分别记下来。

v1 只有 👍/👎/一般 一维，混在一起的两个问题其实答案经常不同：
一篇论文可以**很相关但没用**（结论已知），也可以**不相关但方法有用**（可以直接抄估计量）。
所以 v2 把每条反馈拆成两个 0-2 的量：

    relevance:           2=强相关  1=沾边  0=不相关
    usefulness:          2=很有用  1=一般  0=没用

邮件里是六个按钮，每点一个只更新一维，另一维保留上次的值。
真正的写入端仍然是外部的（Google Sheet / 本地小服务 / CLI），这里只管文件格式与学习逻辑。

兼容 v1：读到 `"votes": {"id": "up"}` 时按 rel=use=2/1/0 折算。
"""

from __future__ import annotations

import json
import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .models import Paper
from .identity import canonical_id
from .storage import atomic_json

LOGGER = logging.getLogger(__name__)

VOTE_UP = "up"
VOTE_MEH = "meh"
VOTE_DOWN = "down"
_LEGACY_VOTE_SCORE = {VOTE_UP: 1.0, VOTE_MEH: 0.0, VOTE_DOWN: -1.0}
_LEGACY_TO_LEVEL = {VOTE_UP: 2, VOTE_MEH: 1, VOTE_DOWN: 0}

RELEVANCE_LEVELS = (0, 1, 2)
USEFULNESS_LEVELS = (0, 1, 2)
_RELEVANCE_SCORE = {2: 1.0, 1: 0.4, 0: -1.0}
_USEFULNESS_SCORE = {2: 1.0, 1: 0.3, 0: -0.6}

CATEGORY_WEIGHT = 0.6
AUTHOR_WEIGHT = 0.35
MAX_BOOST = 1.8
FeedbackRow = tuple[str, int | None, int | None] | tuple[str, int | None, int | None, str]

RELEVANCE_LABELS = {2: "强相关", 1: "沾边", 0: "不相关"}
USEFULNESS_LABELS = {2: "很有用", 1: "一般", 0: "没用"}


def _coerce_level(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value in RELEVANCE_LEVELS else None
    if isinstance(value, str) and value.strip() in {"0", "1", "2"}:
        return int(value.strip())
    return None


def _stamp(value: object) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat(timespec="microseconds")
    except ValueError:
        return ""


@dataclass
class FeedbackRecord:
    """一篇论文上的两维反馈（任意一维可缺）。"""

    relevance: int | None = None
    usefulness: int | None = None
    updated_at: str = ""
    relevance_at: str = ""
    usefulness_at: str = ""

    @property
    def is_empty(self) -> bool:
        return self.relevance is None and self.usefulness is None

    @property
    def score(self) -> float:
        """折成一个标量给排序用：相关性主导，有用性修正。"""

        parts: list[tuple[float, float]] = []
        if self.relevance is not None:
            parts.append((0.65, _RELEVANCE_SCORE[self.relevance]))
        if self.usefulness is not None:
            parts.append((0.35, _USEFULNESS_SCORE[self.usefulness]))
        if not parts:
            return 0.0
        total_weight = sum(weight for weight, _ in parts)
        return sum(weight * value for weight, value in parts) / total_weight

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"updated_at": self.updated_at}
        payload["relevance_at"] = self.relevance_at
        payload["usefulness_at"] = self.usefulness_at
        if self.relevance is not None:
            payload["relevance"] = self.relevance
        if self.usefulness is not None:
            payload["usefulness"] = self.usefulness
        return payload

    def describe(self) -> str:
        labels = []
        if self.relevance is not None:
            labels.append(f"相关：{RELEVANCE_LABELS[self.relevance]}")
        if self.usefulness is not None:
            labels.append(f"有用：{USEFULNESS_LABELS[self.usefulness]}")
        return " · ".join(labels)


@dataclass
class FeedbackStore:
    version: int = 2
    votes: dict[str, FeedbackRecord] = field(default_factory=dict)
    history: dict[str, dict[str, list[str] | str]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.votes

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "votes": {key: record.to_dict() for key, record in self.votes.items()},
            "history": self.history,
        }


def _parse_record(raw: object) -> FeedbackRecord:
    """兼容三种历史写法：v2 字典、v1 字符串、以及只有一维的字典。"""

    if isinstance(raw, str):
        level = _LEGACY_TO_LEVEL.get(raw.strip().lower())
        if level is None:
            return FeedbackRecord()
        return FeedbackRecord(relevance=level, usefulness=level)

    if not isinstance(raw, dict):
        return FeedbackRecord()

    relevance = _coerce_level(raw.get("relevance", raw.get("rel")))
    usefulness = _coerce_level(raw.get("usefulness", raw.get("use")))
    if relevance is None and usefulness is None:
        legacy = _LEGACY_TO_LEVEL.get(str(raw.get("vote") or "").strip().lower())
        if legacy is not None:
            relevance = usefulness = legacy
    return FeedbackRecord(
        relevance=relevance,
        usefulness=usefulness,
        updated_at=_stamp(raw.get("updated_at")),
        relevance_at=_stamp(raw.get("relevance_at", raw.get("updated_at"))),
        usefulness_at=_stamp(raw.get("usefulness_at", raw.get("updated_at"))),
    )


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

    return store_from_dict(raw)


def store_from_dict(raw: Mapping) -> FeedbackStore:
    votes: dict[str, FeedbackRecord] = {}
    raw_votes = raw.get("votes") or {}
    for key, value in (raw_votes.items() if isinstance(raw_votes, dict) else ()):
        record = _parse_record(value)
        if not record.is_empty:
            votes[canonical_id(str(key))] = record

    history: dict[str, dict[str, list[str] | str]] = {}
    raw_history = raw.get("history") or {}
    for key, value in (raw_history.items() if isinstance(raw_history, dict) else ()):
        if not isinstance(value, dict):
            continue
        entry: dict[str, list[str] | str] = {
            "categories": [str(item) for item in value["categories"]] if isinstance(value.get("categories"), list) else [],
            "authors": [str(item) for item in value["authors"]] if isinstance(value.get("authors"), list) else [],
        }
        if value.get("title"):
            entry["title"] = str(value["title"])
        history[canonical_id(str(key))] = entry
    return FeedbackStore(version=2, votes=votes, history=history)


def save_feedback(path: str | Path, store: FeedbackStore) -> None:
    atomic_json(path, store.to_dict())


def save_feedback_merged(path: str | Path, store: FeedbackStore) -> None:
    from .storage import delivery_lock
    with delivery_lock(path):
        save_feedback(path, combine_stores(load_feedback(path), store))


def _remember(store: FeedbackStore, paper: Paper) -> None:
    key = canonical_id(paper.id)
    entry = dict(store.history.get(key) or {})
    for name in ("title", "categories", "authors"):
        value = getattr(paper, name)
        if value:
            entry[name] = value if isinstance(value, str) else list(value)
    store.history[key] = entry


def remember_papers(store: FeedbackStore, papers: Sequence[Paper]) -> None:
    for paper in papers:
        _remember(store, paper)


def combine_stores(older: FeedbackStore, newer: FeedbackStore) -> FeedbackStore:
    """Merge independently timestamped dimensions; never erase richer metadata."""
    result = store_from_dict(older.to_dict())
    for key, meta in newer.history.items():
        entry = result.history.setdefault(canonical_id(key), {})
        entry.update({name: value for name, value in meta.items() if value})
    for key, incoming in newer.votes.items():
        key = canonical_id(key)
        existing = result.votes.get(key) or FeedbackRecord()
        values = dict(existing.__dict__)
        for dimension in ("relevance", "usefulness"):
            stamp = _stamp(getattr(incoming, dimension + "_at"))
            previous = _stamp(getattr(existing, dimension + "_at"))
            if getattr(incoming, dimension) is not None and stamp >= previous:
                values[dimension] = getattr(incoming, dimension)
                values[dimension + "_at"] = stamp
        values["updated_at"] = max(_stamp(existing.updated_at), _stamp(incoming.updated_at))
        result.votes[key] = FeedbackRecord(**values)
    return result


def record_vote(
    store: FeedbackStore,
    paper: Paper,
    *,
    relevance: int | None = None,
    usefulness: int | None = None,
) -> bool:
    """记一次反馈；只传一维时保留另一维的旧值。"""

    relevance = _coerce_level(relevance)
    usefulness = _coerce_level(usefulness)
    if relevance is None and usefulness is None:
        return False
    key = canonical_id(paper.id)
    record = store.votes.get(key) or FeedbackRecord()
    stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    store.votes[key] = FeedbackRecord(
        relevance=record.relevance if relevance is None else relevance,
        usefulness=record.usefulness if usefulness is None else usefulness,
        updated_at=stamp,
        relevance_at=record.relevance_at if relevance is None else stamp,
        usefulness_at=record.usefulness_at if usefulness is None else stamp,
    )
    _remember(store, paper)
    return True


def record_legacy_vote(store: FeedbackStore, paper: Paper, vote: str) -> bool:
    """v1 的三档按钮：两个维度一起置位。"""

    level = _LEGACY_TO_LEVEL.get(str(vote).strip().lower())
    if level is None:
        return False
    return record_vote(store, paper, relevance=level, usefulness=level)


def parse_feedback_csv(text: str) -> list[FeedbackRow]:
    """解析 Google Sheet 导出的 CSV：`item,relevance,usefulness[,timestamp]`。

    表头可有可无；多余列忽略；无法识别的行跳过而不是整份报错。
    """

    rows: list[FeedbackRow] = []
    for cells in csv.reader(io.StringIO(text or "")):
        if not cells or cells[0].strip().startswith("#"):
            continue
        cells = [cell.strip() for cell in cells]
        if len(cells) < 2:
            continue
        item = cells[0]
        if not item or item.lower() in {"item", "id", "paper"}:
            continue
        relevance = _coerce_level(cells[1]) if len(cells) > 1 else None
        usefulness = _coerce_level(cells[2]) if len(cells) > 2 else None
        if relevance is None and usefulness is None:
            continue
        if len(cells) > 3 and cells[3]:
            rows.append((item, relevance, usefulness, cells[3]))
        else:
            rows.append((item, relevance, usefulness))
    return rows


def merge_csv(
    store: FeedbackStore,
    rows: Sequence[FeedbackRow],
) -> int:
    """把表格里的行合并进 store（后来的行覆盖先前的同一维）。返回应用条数。"""

    applied = 0
    for row in rows:
        item, relevance, usefulness = row[:3]
        item = canonical_id(item)
        stamp = ""
        if len(row) > 3:
            try:
                parsed = datetime.fromisoformat(str(row[3]).replace("Z", "+00:00"))
                stamp = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat(timespec="microseconds")
            except ValueError:
                LOGGER.warning("反馈时间戳无效，跳过该行：%s", item)
                continue
        record = store.votes.get(item) or FeedbackRecord()
        values = dict(record.__dict__)
        for name, level in (("relevance", relevance), ("usefulness", usefulness)):
            previous = getattr(record, name + "_at")
            if level is not None and stamp >= previous:
                values[name] = level
                values[name + "_at"] = stamp
        values["updated_at"] = max(record.updated_at, stamp)
        store.votes[item] = FeedbackRecord(**values)
        applied += 1
    return applied


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
    for item_id, record in store.votes.items():
        meta = store.history.get(item_id) or {}
        score = record.score
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


def boost_for(
    paper: Paper,
    categories: Mapping[str, float],
    authors: Mapping[str, float],
) -> float:
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
        breakdown = payload.get("breakdown")
        if breakdown is not None:
            payload["breakdown"] = type(breakdown)(
                keyword=breakdown.keyword,
                zotero=breakdown.zotero,
                category=breakdown.category,
                library=breakdown.library,
                feedback=breakdown.feedback + delta,
            )
        payload["score"] = round(float(paper.score) + delta, 3)
        from .recommender import score_to_rating
        payload["rating"] = score_to_rating(payload["score"])
        adjusted.append(Paper(**payload))
    adjusted.sort(key=lambda item: (item.score, item.published), reverse=True)
    return adjusted


def format_plain_text(store: FeedbackStore) -> str:
    if store.is_empty:
        return "（还没有反馈记录）"
    categories, authors = learned_weights(store)
    relevant = sum(
        1 for record in store.votes.values() if record.relevance is not None and record.relevance >= 1
    )
    useful = sum(
        1 for record in store.votes.values() if record.usefulness is not None and record.usefulness >= 1
    )
    lines = [f"反馈记录：{len(store.votes)} 篇（相关 {relevant} / 有用 {useful}）"]
    if categories:
        top = sorted(categories.items(), key=lambda item: item[1], reverse=True)[:5]
        lines.append("分类偏好：" + "，".join(f"{key} {value:+.2f}" for key, value in top))
    if authors:
        top = sorted(authors.items(), key=lambda item: item[1], reverse=True)[:5]
        lines.append("作者偏好：" + "，".join(f"{key} {value:+.2f}" for key, value in top))
    return "\n".join(lines)
