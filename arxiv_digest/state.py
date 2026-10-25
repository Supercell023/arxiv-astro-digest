"""跨天状态：已读过滤 + 周环比。

GitHub Actions 每次都是干净文件系统，所以要有一份可回写的小状态：
`state.json`（默认写进仓库，随每日 commit 一起回去）。

格式一旦上线就不要再改字段名——用户仓库里已经存着旧版本了。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

LOGGER = logging.getLogger(__name__)

STATE_VERSION = 1
MAX_SEEN = 5000
KEEP_DAYS = 120
WEEK = 7


@dataclass
class State:
    version: int = STATE_VERSION
    # id -> 首次出现的日期（ISO）
    seen: dict[str, str] = field(default_factory=dict)
    # "2026-10-25" -> {"astro-ph.CO": 42, ...}
    daily: dict[str, dict[str, int]] = field(default_factory=dict)
    # 源 id -> 上次抓取日期（按 cadence 控制每日/每周）
    fetched: dict[str, str] = field(default_factory=dict)


@dataclass
class TrendRow:
    label: str
    this_week: int
    last_week: int

    @property
    def delta_pct(self) -> int | None:
        """上周为 0 时返回 None——"从 0 到 3"算百分比会得到无穷大。"""

        if self.last_week <= 0:
            return None
        return round((self.this_week - self.last_week) / self.last_week * 100)

    @property
    def arrow(self) -> str:
        delta = self.delta_pct
        if delta is None or delta == 0:
            return "→"
        return "↑" if delta > 0 else "↓"

    @property
    def label_text(self) -> str:
        delta = self.delta_pct
        if delta is None:
            return f"新增 {self.this_week} 篇"
        if delta == 0:
            return "与上周持平"
        return f"{'上升' if delta > 0 else '下降'} {abs(delta)}%"


def empty_state() -> State:
    return State()


def load_state(path: str | Path) -> State:
    """读不到、读坏、版本不符——统统回退到空状态，绝不抛。"""

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_state()
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("状态文件不可用（%s）：%s，按空状态处理。", path, exc)
        return empty_state()

    if not isinstance(raw, dict):
        return empty_state()
    if int(raw.get("version") or 0) != STATE_VERSION:
        LOGGER.warning("状态文件版本不是 %d，忽略旧状态。", STATE_VERSION)
        return empty_state()

    seen = raw.get("seen")
    daily = raw.get("daily")
    fetched = raw.get("fetched")
    return State(
        version=STATE_VERSION,
        seen={str(k): str(v) for k, v in seen.items()} if isinstance(seen, dict) else {},
        daily={str(k): {str(c): int(n) for c, n in v.items()} for k, v in daily.items() if isinstance(v, dict)}
        if isinstance(daily, dict)
        else {},
        fetched={str(k): str(v) for k, v in fetched.items()} if isinstance(fetched, dict) else {},
    )


def save_state(state: State, path: str | Path) -> bool:
    """原子写：先写 `.tmp` 再替换，避免半个文件把明天的运行毁掉。"""

    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(state.__dict__, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, target)
        return True
    except OSError as exc:
        LOGGER.warning("状态写入失败（%s）：%s", path, exc)
        return False


def filter_unseen(state: State, items):
    """过滤掉已经推送过的条目；没有 id 的条目一律保留。"""

    return [item for item in items if not item.id or item.id not in state.seen]


def mark_seen(state: State, items, *, day: str) -> None:
    for item in items:
        if item.id:
            state.seen.setdefault(item.id, day)


def prune(state: State, *, today: date, keep_days: int = KEEP_DAYS, max_seen: int = MAX_SEEN) -> None:
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    state.seen = {key: stamp for key, stamp in state.seen.items() if stamp >= cutoff}
    state.daily = {key: value for key, value in state.daily.items() if key >= cutoff}
    if len(state.seen) > max_seen:
        newest = sorted(state.seen.items(), key=lambda kv: kv[1], reverse=True)[:max_seen]
        state.seen = dict(newest)


def should_fetch(cadence: str, last: str | None, today: date) -> bool:
    """按 cadence 判断今天要不要抓这个源。

    `daily`：今天没抓过就抓；`weekly`：距上次满 7 天才抓。
    日期读不出来（手工改坏）就当作没抓过——宁可多抓一次，不要漏一周。
    """

    if not last:
        return True
    try:
        last_date = date.fromisoformat(last[:10])
    except ValueError:
        return True
    if cadence == "weekly":
        return (today - last_date).days >= 7
    return last_date != today


def due_ids(state: State, cadences: dict[str, str], *, today: date) -> set[str]:
    """今天该跳过的源 id。没有任何状态记录的源一律照抓。"""

    return {
        key
        for key, cadence in cadences.items()
        if not should_fetch(cadence, state.fetched.get(key), today)
    }


def mark_fetched(state: State, keys, *, day: str) -> None:
    for key in keys:
        if key:
            state.fetched[str(key)] = day


def record_day(state: State, *, day: str, categories: list[str]) -> None:
    """记录当天每个分类的论文数（同一篇只算主分类，避免 cross-list 灌水）。"""

    bucket = state.daily.setdefault(day, {})
    for category in categories:
        bucket[category] = bucket.get(category, 0) + 1


def weekly_trend(
    state: State,
    *,
    today: date,
    limit: int = 4,
    min_total: int = 2,
) -> list[TrendRow]:
    """本周 vs 上周的每分类投稿量。数据不足 2 天时返回空——不假装有趋势。"""

    def window(start_offset: int, end_offset: int) -> dict[str, int]:
        totals: dict[str, int] = {}
        for offset in range(start_offset, end_offset):
            day = (today - timedelta(days=offset)).isoformat()
            for category, count in state.daily.get(day, {}).items():
                totals[category] = totals.get(category, 0) + count
        return totals

    this_week = window(0, WEEK)
    last_week = window(WEEK, 2 * WEEK)
    if not this_week or len(state.daily) < 2:
        return []

    rows = [
        TrendRow(label=label, this_week=count, last_week=last_week.get(label, 0))
        for label, count in this_week.items()
        if count >= min_total or last_week.get(label, 0) >= min_total
    ]
    rows.sort(key=lambda row: (row.this_week, row.label), reverse=True)
    return rows[:limit]


def today_str(now: datetime | None = None) -> str:
    return (now or datetime.now()).date().isoformat()
