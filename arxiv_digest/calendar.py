"""领域事件日历：把 config/events.yaml 与 config/conferences.yaml 折算成"还有几天"。

设计原则（三条，都是被前面检查出来的教训）：

1. **只依赖文件，不依赖网络。** 这一层永远不会因为 arXiv/OpenAI/机构站点挂掉而失败。
2. **任何解析错误都降级为空日历。** 邮件发不出去比少一个日历块严重得多，所以这里
   所有异常都在内部吞掉并记日志，绝不向上抛。
3. **日期允许为 null，精度分三档。** 大量条目是"知道在等、但不知道哪天"（Rubin DP2、
   PFS DR1、CSST 发射）。把「2027 年发射」渲染成「还有 434 天」是假的精确，
   所以 month/year 精度不给倒计时，只给区间表述。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# 事件类型 → 邮件里显示的中文标签
_KIND_LABELS: dict[str, str] = {
    "data_release": "数据发布",
    "conference": "会议",
    "deadline": "截止",
    "launch": "发射",
    "milestone": "里程碑",
}

# date_precision 支持中英文两种写法，方便手写 YAML 时不出错
_PRECISION_ALIASES: dict[str, str] = {
    "日": "day",
    "天": "day",
    "day": "day",
    "月": "month",
    "month": "month",
    "年": "year",
    "year": "year",
}

_PRECISION_DAY = "day"
_PRECISION_MONTH = "month"
_PRECISION_YEAR = "year"

# urgency 取值
_URGENCY_TODAY = "today"
_URGENCY_WEEK = "week"
_URGENCY_SOON = "soon"
_URGENCY_PAST = "past"
_URGENCY_TBA = "tba"


@dataclass(frozen=True)
class CalendarLine:
    """一条已经算好倒计时的日程。所有字段都是展示态，模板直接取用。"""

    title: str
    kind_label: str
    when_label: str
    countdown_label: str
    confidence: str
    url: str
    location: str
    note: str
    urgency: str


@dataclass(frozen=True)
class CalendarBundle:
    """一封信里要显示的日程集合，已按紧迫程度分好组。"""

    highlights: tuple[CalendarLine, ...]
    countdown: tuple[CalendarLine, ...]
    recent: tuple[CalendarLine, ...]
    undated: tuple[CalendarLine, ...]
    next_far: CalendarLine | None

    @property
    def is_empty(self) -> bool:
        return not (self.highlights or self.countdown or self.recent or self.undated)

    @property
    def rows(self) -> tuple[CalendarLine, ...]:
        """邮件主区块要渲染的行：先今天/本周，再远期倒计时。"""
        return self.highlights + self.countdown

    @property
    def undated_titles(self) -> str:
        """「日期未公布」那一行：超过 4 项就折叠，否则会把页尾撑爆。"""
        shown = [line.title for line in self.undated[:4]]
        text = "、".join(shown)
        if len(self.undated) > len(shown):
            text += f" 等 {len(self.undated)} 项"
        return text


def empty_bundle() -> CalendarBundle:
    return CalendarBundle(
        highlights=(),
        countdown=(),
        recent=(),
        undated=(),
        next_far=None,
    )


_EXPECTED_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")
_EXPECTED_YEAR_RE = re.compile(r"^(\d{4})$")


def _format_expected_month(raw: str) -> str:
    """把 '2027-01' 渲染成 '2027 年 1 月'；认不出的原样返回，不猜。"""
    text = raw.strip()
    month_match = _EXPECTED_MONTH_RE.match(text)
    if month_match:
        return f"{month_match.group(1)} 年 {int(month_match.group(2))} 月"
    year_match = _EXPECTED_YEAR_RE.match(text)
    if year_match:
        return f"{year_match.group(1)} 年"
    return text


def _coerce_date(value: Any) -> date | None:
    """把 YAML 里的日期字段揉成 date 或 None。容忍字符串、datetime、空值。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"null", "none", "tbd", "待定"}:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            logger.warning("日历条目里有无法解析的日期：%r", value)
            return None
    return None


def _coerce_precision(raw: Mapping[str, Any], when: date | None) -> str:
    declared = raw.get("date_precision")
    if isinstance(declared, str):
        mapped = _PRECISION_ALIASES.get(declared.strip().lower())
        if mapped:
            return mapped
    return _PRECISION_DAY if when is not None else _PRECISION_MONTH


def _text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


def _format_span(start: date, end: date | None, precision: str) -> str:
    """把起止日期渲染成人话：10 月 27–31 日 / 2027 年 6 月 / 2027 年。"""
    if precision == _PRECISION_YEAR:
        return f"{start.year} 年"
    if precision == _PRECISION_MONTH:
        return f"{start.year} 年 {start.month} 月"
    if end is not None and end != start:
        if end.year == start.year and end.month == start.month:
            return f"{start.month} 月 {start.day}–{end.day} 日"
        if end.year == start.year:
            return f"{start.month} 月 {start.day} 日–{end.month} 月 {end.day} 日"
        return f"{start.year} 年 {start.month} 月 {start.day} 日–{end.year} 年 {end.month} 月 {end.day} 日"
    return f"{start.month} 月 {start.day} 日"


def _format_countdown(days: int, precision: str) -> str:
    """day 精度给精确倒计时；month/year 精度只给约数或完全不给。"""
    if precision == _PRECISION_YEAR:
        return ""
    if precision == _PRECISION_MONTH:
        if days < 0:
            return f"约 {-days} 天前"
        if days == 0:
            return "本月"
        return f"约 {days} 天"
    if days == 0:
        return "今天"
    if days > 0:
        return f"D-{days}"
    return f"{-days} 天前"


def _build_line(
    *,
    title: str,
    kind: str,
    when: date | None,
    end: date | None,
    precision: str,
    confidence: str,
    url: str,
    location: str,
    note: str,
    today: date,
    horizon_days: int,
    recent_days: int,
    expected_month: str,
) -> tuple[str, CalendarLine | None]:
    """返回 (urgency, line)。urgency 决定这条进哪个分组。"""
    kind_label = _KIND_LABELS.get(kind, kind or "事件")

    if when is None:
        # 没有确切日期：只保留有"预计月份"或明确标了待定的，避免无信息噪音
        if not expected_month and confidence != "待定":
            return _URGENCY_TBA, None
        when_label = f"预计 {_format_expected_month(expected_month)}" if expected_month else "日期未公布"
        line = CalendarLine(
            title=title,
            kind_label=kind_label,
            when_label=when_label,
            countdown_label="",
            confidence=confidence or "待定",
            url=url,
            location=location,
            note=note,
            urgency=_URGENCY_TBA,
        )
        return _URGENCY_TBA, line

    days = (when - today).days
    when_label = _format_span(when, end, precision)

    if days < 0 and days < -recent_days:
        return _URGENCY_PAST, None  # 已归档，不显示

    if days < 0:
        urgency = _URGENCY_PAST
    elif days == 0 or (precision == _PRECISION_DAY and days <= 7):
        urgency = _URGENCY_TODAY if days == 0 else _URGENCY_WEEK
    elif precision == _PRECISION_DAY and days <= horizon_days:
        urgency = _URGENCY_SOON
    elif precision != _PRECISION_DAY and days <= horizon_days:
        urgency = _URGENCY_SOON
    else:
        urgency = _URGENCY_PAST  # 超出视野，由调用方决定是否作为 next_far

    line = CalendarLine(
        title=title,
        kind_label=kind_label,
        when_label=when_label,
        countdown_label=_format_countdown(days, precision),
        confidence=confidence or "",
        url=url,
        location=location,
        note=note,
        urgency=urgency,
    )
    return urgency, line


def _iter_event_lines(
    payload: Any,
    *,
    today: date,
    horizon_days: int,
    recent_days: int,
) -> list[tuple[int, str, CalendarLine]]:
    """处理 events.yaml。返回 (days_until, urgency, line) 便于排序。"""
    if not isinstance(payload, Mapping):
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        return []

    results: list[tuple[int, str, CalendarLine]] = []
    for raw in events:
        if not isinstance(raw, Mapping):
            continue
        title = _text(raw, "title")
        if not title:
            continue
        kind = _text(raw, "kind") or "milestone"
        when = _coerce_date(raw.get("date"))
        end = _coerce_date(raw.get("date_end"))
        precision = _coerce_precision(raw, when)
        urgency, line = _build_line(
            title=title,
            kind=kind,
            when=when,
            end=end,
            precision=precision,
            confidence=_text(raw, "confidence"),
            url=_text(raw, "url"),
            location=_text(raw, "location"),
            note=_text(raw, "note"),
            today=today,
            horizon_days=horizon_days,
            recent_days=recent_days,
            expected_month=_text(raw, "expected_month"),
        )
        if line is None:
            continue
        days = (when - today).days if when is not None else 10**6
        results.append((days, urgency, line))
    return results


def _iter_conference_lines(
    payload: Any,
    *,
    today: date,
    horizon_days: int,
    recent_days: int,
) -> list[tuple[int, str, CalendarLine]]:
    """处理 conferences.yaml。系列名会拼进标题，否则「第29届」看不出是什么会。"""
    if not isinstance(payload, Mapping):
        return []

    series_names: dict[str, str] = {}
    series_raw = payload.get("series")
    if isinstance(series_raw, list):
        for item in series_raw:
            if isinstance(item, Mapping):
                sid = _text(item, "id")
                if sid:
                    series_names[sid] = _text(item, "name") or sid

    instances = payload.get("instances")
    if not isinstance(instances, list):
        return []

    results: list[tuple[int, str, CalendarLine]] = []
    for raw in instances:
        if not isinstance(raw, Mapping):
            continue
        series_id = _text(raw, "series")
        edition = _text(raw, "edition")
        base = series_names.get(series_id, series_id)
        title = _text(raw, "title") or (f"{edition} · {base}" if edition else base)
        when = _coerce_date(raw.get("date_start"))
        end = _coerce_date(raw.get("date_end"))
        precision = _coerce_precision(raw, when)
        urgency, line = _build_line(
            title=title,
            kind="conference",
            when=when,
            end=end,
            precision=precision,
            confidence=_text(raw, "confidence"),
            url=_text(raw, "url") or _text(raw, "indico"),
            location=_text(raw, "location"),
            note=_text(raw, "note"),
            today=today,
            horizon_days=horizon_days,
            recent_days=recent_days,
            expected_month=_text(raw, "expected_month"),
        )
        if line is None:
            continue
        days = (when - today).days if when is not None else 10**6
        results.append((days, urgency, line))
    return results


def _resolve_path(raw_path: str) -> Path | None:
    """相对路径先按 CWD 找，再退到包目录的上一级（兼容从任意目录调用）。"""
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    if candidate.exists():
        return candidate
    fallback = Path(__file__).resolve().parent.parent / candidate
    if fallback.exists():
        return fallback
    return None


def _load_yaml(raw_path: str) -> Any:
    path = _resolve_path(raw_path)
    if path is None:
        logger.warning("找不到日历文件：%s（将跳过）", raw_path)
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except Exception:
        logger.exception("解析日历文件失败：%s（将跳过）", raw_path)
        return None


def load_calendar(
    events_path: str,
    conferences_path: str,
    *,
    today: date | None = None,
    horizon_days: int = 120,
    recent_days: int = 14,
) -> CalendarBundle:
    """读两个 YAML，折算成可以直接喂模板的 CalendarBundle。

    任何一步失败都返回空 bundle —— 日历是增值项，不能拖垮主流程。
    """
    try:
        current = today or date.today()
        entries: list[tuple[int, str, CalendarLine]] = []
        entries.extend(
            _iter_event_lines(
                _load_yaml(events_path),
                today=current,
                horizon_days=horizon_days,
                recent_days=recent_days,
            )
        )
        entries.extend(
            _iter_conference_lines(
                _load_yaml(conferences_path),
                today=current,
                horizon_days=horizon_days,
                recent_days=recent_days,
            )
        )
        return _assemble(entries, current, horizon_days)
    except Exception:
        logger.exception("构建日历时发生未预期错误，已降级为空日历")
        return empty_bundle()


def _assemble(
    entries: list[tuple[int, str, CalendarLine]],
    today: date,
    horizon_days: int,
) -> CalendarBundle:
    today_lines: list[CalendarLine] = []
    week_lines: list[tuple[int, CalendarLine]] = []
    soon_lines: list[tuple[int, CalendarLine]] = []
    past_lines: list[tuple[int, CalendarLine]] = []
    tba_lines: list[CalendarLine] = []
    far_lines: list[tuple[int, CalendarLine]] = []

    for days, urgency, line in entries:
        if urgency == _URGENCY_TODAY:
            today_lines.append(line)
        elif urgency == _URGENCY_WEEK:
            week_lines.append((days, line))
        elif urgency == _URGENCY_SOON:
            soon_lines.append((days, line))
        elif urgency == _URGENCY_TBA:
            tba_lines.append(line)
        elif days < 0:
            past_lines.append((days, line))
        else:
            far_lines.append((days, line))

    week_lines.sort(key=lambda pair: pair[0])
    soon_lines.sort(key=lambda pair: pair[0])
    past_lines.sort(key=lambda pair: -pair[0])  # 最近过去的排前面
    far_lines.sort(key=lambda pair: pair[0])

    return CalendarBundle(
        highlights=tuple(today_lines + [line for _, line in week_lines]),
        countdown=tuple(line for _, line in soon_lines),
        recent=tuple(line for _, line in past_lines),
        undated=tuple(tba_lines),
        next_far=far_lines[0][1] if far_lines else None,
    )


def format_plain_text(bundle: CalendarBundle) -> str:
    """给 preview_digest.py 用：把日历打成终端可读的几行。"""
    if bundle.is_empty:
        return "（日历为空：检查 config/events.yaml 与 config/conferences.yaml）"

    lines: list[str] = []
    for line in bundle.rows:
        badge = f" [{line.countdown_label}]" if line.countdown_label else ""
        where = f" @{line.location}" if line.location else ""
        lines.append(f"- {line.when_label}{badge} {line.kind_label}｜{line.title}{where}")
    if bundle.recent:
        lines.append("  刚过去：" + "；".join(f"{line.when_label} {line.title}" for line in bundle.recent))
    if bundle.undated:
        lines.append("  日期未公布：" + bundle.undated_titles)
    if bundle.next_far is not None:
        lines.append(f"  更远：{bundle.next_far.when_label} {bundle.next_far.title}")
    return "\n".join(lines)
