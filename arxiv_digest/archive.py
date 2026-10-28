"""长期热点档案：把抓到的论文按周写进 Google 表格。

口径（详见 ``docs/ARCHIVE.md``）：

* 周从周日零点开始，与周报窗口一致；``week_start`` 记那一周的周日。
* ``share`` = 当周该主题命中篇数 / 当周采样篇数。一篇多主题会占多行，
  所以同一周的 share 之和可以超过 100%，它不是饼图。
* ``delta_pct`` 只在上一周有样本时给值；上一周为 0 时留空，避免除零。
* ``ma4`` 是含当周在内的四周移动平均（缺周只按已有周平均），用来抵消
  arXiv 周末与节假日的公告节律。
* ``concepts`` 只来自精读篇，是子集，不代表全量频率。

写表走 Apps Script Web App：HMAC-SHA256 签名 + 时间戳窗口 + 脚本锁；
传输只用标准库（urllib / hmac / base64），不引入新依赖。预览永远不写表。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from .identity import canonical_id
from .packs import Pack, dispatch
from .recommender import _normalize_for_matching

USER_AGENT = "arxiv-astro-digest/archive"
DEFAULT_TIMEOUT = 20
DEFAULT_MAX_ROWS = 400
DEFAULT_SNIPPET_CHARS = 160
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

PAPERS_SHEET = "papers"
TOPICS_SHEET = "weekly_topics"
OVERVIEW_SHEET = "weekly_overview"
DICT_SHEET = "dict"

PAPERS_KEY = ("week_start", "id")
TOPICS_KEY = ("week_start", "topic")
OVERVIEW_KEY = ("week_start",)
DICT_KEY = ("topic",)

PAPERS_HEADERS = (
    "week_start", "week_label", "date", "id", "doi", "venue", "source_kind", "pack",
    "categories", "topics", "keywords", "title", "title_zh", "first_author", "n_authors",
    "summary_snippet", "score", "rating", "s_keyword", "s_zotero", "s_category", "s_library",
    "s_feedback", "shown", "deep", "relevance", "relevance_kind", "verdict", "concepts",
    "n_numbers", "n_red_flags", "basis", "confidence",
)

TOPICS_HEADERS = (
    "week_start", "week_label", "topic", "pack", "papers", "share", "prev_papers", "delta_pct",
    "ma4", "arxiv_papers", "journal_papers", "mean_score", "top_concepts", "first_week", "finalized",
)

OVERVIEW_HEADERS = (
    "week_start", "week_label", "sampled", "arxiv_papers", "journal_papers", "unique_categories",
    "deep_reads", "venues", "top5_topics", "top5_concepts", "rising", "falling", "finalized",
)

DICT_HEADERS = ("topic", "aliases", "pack", "source", "first_week", "last_week", "total_papers")


class ArchiveError(RuntimeError):
    """表格端点不可用、拒绝请求或返回了无法解析的内容。"""


@dataclass(frozen=True)
class WeekWindow:
    start: date
    end: date  # exclusive

    @property
    def label(self) -> str:
        return f"{self.start} 至 {self.end - timedelta(days=1)}"

    @property
    def start_iso(self) -> str:
        return self.start.isoformat()


def week_window(day: date) -> WeekWindow:
    """返回包含 ``day`` 的、以周日为界的那一周。"""

    start = day - timedelta(days=(day.weekday() + 1) % 7)
    return WeekWindow(start, start + timedelta(days=7))


def week_for_timestamp(value: datetime, timezone_name: str = "UTC") -> WeekWindow:
    stamp = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return week_window(stamp.astimezone(ZoneInfo(timezone_name)).date())


def ready(config: Any) -> bool:
    """档案写入是否可用：开关打开且 URL、令牌都非空。"""

    return bool(
        getattr(config, "archive_enabled", False)
        and str(getattr(config, "archive_webhook_url", "") or "").strip()
        and str(getattr(config, "archive_token", "") or "")
    )


# --- 打标 -----------------------------------------------------------------


def topic_table(packs: Sequence[Pack]) -> dict[str, tuple[str, tuple[str, ...]]]:
    """主题 -> (所属领域包 id, 别名元组)。同名主题取第一个定义。"""

    table: dict[str, tuple[str, tuple[str, ...]]] = {}
    for pack in packs:
        for topic, aliases in pack.topics:
            table.setdefault(str(topic), (pack.id, tuple(str(alias) for alias in aliases)))
    return table


def _hit(normalized: str, alias: str) -> bool:
    term = _normalize_for_matching(str(alias)).strip()
    if not term:
        return False
    if " " in term:
        return term in normalized
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", normalized) is not None


def match_topics(packs: Sequence[Pack], text: str) -> list[str]:
    normalized = _normalize_for_matching(text)
    hits = {
        str(topic)
        for pack in packs
        for topic, aliases in pack.topics
        if any(_hit(normalized, alias) for alias in aliases)
    }
    return sorted(hits)


def match_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    normalized = _normalize_for_matching(text)
    return sorted({str(word) for word in keywords if _hit(normalized, word)})


# --- 行构建 ---------------------------------------------------------------


def _cell(value: Any, limit: int = 0) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value if value is not None else "")).strip()
    if limit:
        text = text[:limit]
    if text.startswith("="):
        # 表格会把 = 开头的单元格当公式；这里只做文本，避免被当成公式执行。
        text = "'" + text
    return text


def _split(value: Any) -> list[str]:
    return [part for part in str(value or "").split(";") if part]


def _int(value: Any) -> int:
    try:
        return int(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _published(paper: Any) -> datetime:
    stamp = paper.published
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def build_rows(
    *,
    papers: Sequence[Any],
    ranked: Sequence[Any],
    enriched: Sequence[Any],
    packs: Sequence[Pack],
    keywords: Iterable[str],
    timezone_name: str = "UTC",
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> list[list[str]]:
    """把一批论文压成 ``papers`` 表的行（列顺序 = PAPERS_HEADERS）。"""

    zone = ZoneInfo(timezone_name)
    shown = {canonical_id(paper.id) for paper in ranked}
    analyzed = {
        canonical_id(paper.id): paper.analysis
        for paper in enriched
        if getattr(paper, "analysis", None) is not None
    }
    rows: list[list[str]] = []
    for paper in papers:
        pid = canonical_id(paper.id)
        published = _published(paper).astimezone(zone)
        week = week_window(published.date())
        text = f"{paper.title} {paper.title_zh} {paper.summary}"
        pack = dispatch(packs, paper.categories, hint=paper.pack_hint, title=paper.title)
        pack_id = "" if pack is None or pack.is_generic else pack.id
        analysis = analyzed.get(pid)
        breakdown = paper.breakdown
        rows.append([
            week.start_iso,
            week.label,
            published.date().isoformat(),
            _cell(paper.id),
            _cell(paper.doi),
            _cell(paper.venue),
            "journal" if str(paper.id).startswith("doi:") else "arxiv",
            pack_id,
            ",".join(paper.categories),
            ";".join(match_topics(packs, text)),
            ";".join(match_keywords(text, keywords)),
            _cell(paper.title, 300),
            _cell(paper.title_zh, 300),
            _cell(paper.authors[0] if paper.authors else "", 120),
            str(len(paper.authors)),
            _cell(paper.summary)[:snippet_chars],
            f"{paper.score:.2f}",
            str(paper.rating),
            f"{breakdown.keyword:+.2f}",
            f"{breakdown.zotero:+.2f}",
            f"{breakdown.category:+.2f}",
            f"{breakdown.library:+.2f}",
            f"{breakdown.feedback:+.2f}",
            "1" if pid in shown else "0",
            "1" if analysis is not None else "0",
            f"{analysis.relevance:.2f}" if analysis else "",
            _cell(analysis.relevance_kind) if analysis else "",
            _cell(analysis.verdict, 200) if analysis else "",
            ";".join(concept.term for concept in analysis.concepts) if analysis else "",
            str(len(analysis.numbers)) if analysis else "",
            str(len(analysis.red_flags)) if analysis else "",
            _cell(analysis.basis) if analysis else "",
            _cell(analysis.confidence) if analysis else "",
        ])
    return rows


def _row_dict(headers: Sequence[str], row: Sequence[Any]) -> dict[str, str]:
    values = list(row) + [""] * (len(headers) - len(row))
    return {str(name): _cell(values[index]) for index, name in enumerate(headers)}


# --- 聚合 -----------------------------------------------------------------


def _history_by_week(history: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    table: dict[str, dict[str, int]] = {}
    for row in history:
        week_start = str(row.get("week_start") or "")
        topic = str(row.get("topic") or "")
        if not week_start or not topic:
            continue
        table.setdefault(week_start, {})[topic] = _int(row.get("papers"))
    return table


def _moving_average(topic: str, week: WeekWindow, count: int,
                    history: Mapping[str, Mapping[str, int]]) -> float:
    weeks = [week.start_iso] + [(week.start - timedelta(days=7 * offset)).isoformat()
                                for offset in (1, 2, 3)]
    values: list[int] = []
    for week_start in weeks:
        if week_start == week.start_iso:
            values.append(count)
        elif week_start in history:
            values.append(history[week_start].get(topic, 0))
    return sum(values) / len(values) if values else 0.0


def _first_week(topic: str, week_start: str, history: Sequence[Mapping[str, Any]]) -> str:
    known = [str(row.get("first_week") or "") for row in history if str(row.get("topic") or "") == topic]
    known = [value for value in known if value]
    known.append(week_start)
    return min(known)


def summarize_week(
    *,
    week: WeekWindow,
    rows: Sequence[Mapping[str, Any]],
    prev_rows: Sequence[Mapping[str, Any]],
    history_topics: Sequence[Mapping[str, Any]],
    finalized: bool,
    topic_packs: Mapping[str, str],
    topic_aliases: Mapping[str, Sequence[str]] | None = None,
) -> tuple[list[list[str]], list[list[str]], list[str]]:
    """返回 (weekly_topics 行, dict 行, weekly_overview 行)。"""

    start = week.start_iso
    sampled = len(rows)
    arxiv = sum(1 for row in rows if row.get("source_kind") == "arxiv")
    deep = sum(1 for row in rows if _int(row.get("deep")))
    categories = {
        category.strip()
        for row in rows
        for category in str(row.get("categories") or "").split(",")
        if category.strip()
    }
    venues = Counter(
        str(row.get("venue") or ("arXiv" if row.get("source_kind") == "arxiv" else "其他"))
        for row in rows
    )
    current_counts = Counter(topic for row in rows for topic in _split(row.get("topics")))
    prev_counts = Counter(topic for row in prev_rows for topic in _split(row.get("topics")))
    history = _history_by_week(history_topics)

    topic_rows: list[list[str]] = []
    deltas: list[tuple[str, float, int, int]] = []
    for topic in sorted(set(current_counts) | set(prev_counts)):
        count = current_counts.get(topic, 0)
        prev = prev_counts.get(topic, 0)
        share = round(count / sampled, 4) if sampled else 0.0
        topic_papers = [row for row in rows if topic in _split(row.get("topics"))]
        topic_arxiv = sum(1 for row in topic_papers if row.get("source_kind") == "arxiv")
        mean_score = (round(sum(_float(row.get("score")) for row in topic_papers) / len(topic_papers), 2)
                      if topic_papers else "")
        concepts = Counter(
            term
            for row in topic_papers
            if _int(row.get("deep"))
            for term in _split(row.get("concepts"))
        )
        delta: float | str = round((count - prev) / prev * 100, 1) if prev > 0 else ""
        if delta != "":
            deltas.append((topic, float(delta), count, prev))
        topic_rows.append([
            start,
            week.label,
            topic,
            str(topic_packs.get(topic) or ""),
            str(count),
            f"{share:.4f}",
            str(prev),
            "" if delta == "" else f"{delta:+.1f}",
            f"{_moving_average(topic, week, count, history):.2f}",
            str(topic_arxiv),
            str(count - topic_arxiv),
            "" if mean_score == "" else f"{mean_score:.2f}",
            ";".join(f"{term}={hits}" for term, hits in concepts.most_common(3)),
            _first_week(topic, start, history_topics),
            "1" if finalized else "0",
        ])

    all_concepts = Counter(
        term for row in rows if _int(row.get("deep")) for term in _split(row.get("concepts"))
    )
    rising = [
        (topic, delta) for topic, delta, count, prev in sorted(deltas, key=lambda item: -item[1])
        if delta > 0 and count >= 2 and prev >= 2
    ][:3]
    falling = [
        (topic, delta) for topic, delta, count, prev in sorted(deltas, key=lambda item: item[1])
        if delta < 0 and count >= 2 and prev >= 2
    ][:3]
    overview = [
        start,
        week.label,
        str(sampled),
        str(arxiv),
        str(sampled - arxiv),
        str(len(categories)),
        str(deep),
        ";".join(f"{venue}={count}" for venue, count in venues.most_common(5)),
        ";".join(f"{topic}={count}" for topic, count in current_counts.most_common(5)),
        ";".join(f"{term}={hits}" for term, hits in all_concepts.most_common(5)),
        ";".join(f"{topic}={delta:+.0f}%" for topic, delta in rising),
        ";".join(f"{topic}={delta:+.0f}%" for topic, delta in falling),
        "1" if finalized else "0",
    ]
    return topic_rows, _dict_rows(start, current_counts, history_topics, topic_packs, topic_aliases or {}), overview


def _dict_rows(
    week_start: str,
    counts: Mapping[str, int],
    history_topics: Sequence[Mapping[str, Any]],
    topic_packs: Mapping[str, str],
    topic_aliases: Mapping[str, Sequence[str]],
) -> list[list[str]]:
    totals: Counter[str] = Counter()
    first: dict[str, str] = {}
    last: dict[str, str] = {}
    for row in history_topics:
        topic = str(row.get("topic") or "")
        other_week = str(row.get("week_start") or "")
        if not topic or not other_week or other_week == week_start:
            continue
        totals[topic] += _int(row.get("papers"))
        first[topic] = min(first.get(topic, other_week), other_week)
        last[topic] = max(last.get(topic, other_week), other_week)
    for topic, count in counts.items():
        totals[topic] += count
        first[topic] = min(first.get(topic, week_start), week_start)
        last[topic] = max(last.get(topic, week_start), week_start)

    rows: list[list[str]] = []
    for topic in sorted(totals):
        rows.append([
            topic,
            ";".join(str(alias) for alias in topic_aliases.get(topic, ())),
            str(topic_packs.get(topic) or ""),
            "pack-topic",
            first.get(topic, ""),
            last.get(topic, ""),
            str(totals[topic]),
        ])
    return rows


# --- 传输 -----------------------------------------------------------------


class Transport(Protocol):
    def read(self, sheet: str, where: Mapping[str, str] | None = None) -> list[dict[str, str]]: ...

    def upsert(self, sheet: str, headers: Sequence[str], rows: Sequence[Sequence[str]],
               key: Sequence[str]) -> int: ...

    def health(self) -> dict[str, Any]: ...


class HttpTransport:
    """签名 POST 到 Apps Script；只用标准库。"""

    def __init__(self, url: str, token: str, timeout: int = DEFAULT_TIMEOUT,
                 max_rows: int = DEFAULT_MAX_ROWS) -> None:
        self.url = str(url).strip()
        self.token = str(token)
        self.timeout = max(5, int(timeout))
        self.max_rows = max(1, int(max_rows))

    def _sign(self, raw: bytes) -> str:
        return hmac.new(self.token.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    def _call(self, action: str, **fields: Any) -> dict[str, Any]:
        body = {"action": action, **fields}
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        envelope = {
            "ts": int(time.time()),
            "sig": self._sign(raw),
            "payload": base64.b64encode(raw).decode("ascii"),
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(envelope).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except Exception as exc:  # noqa: BLE001 - 统一转成 ArchiveError
            raise ArchiveError(f"表格端点不可用：{type(exc).__name__}") from exc
        if len(data) > MAX_RESPONSE_BYTES:
            raise ArchiveError("表格端点返回内容过大")
        try:
            payload = json.loads(data.decode("utf-8"))
        except ValueError as exc:
            raise ArchiveError("表格端点返回的不是 JSON（检查部署权限是否设为「任何人」）") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            reason = payload.get("error") if isinstance(payload, dict) else "unknown"
            raise ArchiveError(f"表格端点拒绝：{reason}")
        return payload

    def read(self, sheet: str, where: Mapping[str, str] | None = None) -> list[dict[str, str]]:
        payload = self._call("read", sheet=sheet, where=dict(where or {}))
        headers = [str(item) for item in payload.get("headers") or []]
        return [_row_dict(headers, row) for row in payload.get("rows") or []]

    def upsert(self, sheet: str, headers: Sequence[str], rows: Sequence[Sequence[str]],
               key: Sequence[str]) -> int:
        written = 0
        for start in range(0, len(rows), self.max_rows):
            chunk = [[str(value) for value in row] for row in rows[start:start + self.max_rows]]
            payload = self._call("upsert", sheet=sheet, headers=list(headers), key=list(key), rows=chunk)
            written += _int(payload.get("written"))
        return written

    def health(self) -> dict[str, Any]:
        return self._call("health")


# --- 入口 -----------------------------------------------------------------


def _merge_rows(existing: Sequence[Mapping[str, str]],
                new_rows: Sequence[Sequence[str]]) -> tuple[list[dict[str, str]], list[list[str]]]:
    merged: dict[tuple[str, str], dict[str, str]] = {}
    for row in existing:
        merged[(str(row.get("week_start") or ""), str(row.get("id") or ""))] = dict(row)
    known = set(merged)
    fresh: list[list[str]] = []
    for row in new_rows:
        item = _row_dict(PAPERS_HEADERS, row)
        pair = (item["week_start"], item["id"])
        if pair not in known:
            fresh.append(list(row))
        merged[pair] = item
    return sorted(merged.values(), key=lambda item: (item["week_start"], item["id"])), fresh


def record(
    config: Any,
    rows: Sequence[Sequence[str]],
    *,
    today: date | None = None,
    timezone_name: str | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """写出本周与上周的档案；任何传输问题都会抛 ArchiveError 由调用方兜住。"""

    if not rows:
        return {"ok": True, "written": 0, "weeks": [], "skipped": "no rows"}

    timezone_name = timezone_name or str(getattr(config, "digest_timezone", "UTC"))
    today = today or datetime.now(ZoneInfo(timezone_name)).date()
    handler: Transport = transport or HttpTransport(
        getattr(config, "archive_webhook_url", ""),
        getattr(config, "archive_token", ""),
        getattr(config, "archive_timeout", DEFAULT_TIMEOUT),
        getattr(config, "archive_max_rows", DEFAULT_MAX_ROWS),
    )

    from .packs import load_packs, select_packs

    try:
        packs = select_packs(load_packs(getattr(config, "packs_dir", "config/packs")),
                             getattr(config, "fields", ()))
    except (OSError, ValueError):
        packs = []
    table = topic_table(packs)
    topic_packs = {topic: pack_id for topic, (pack_id, _) in table.items()}
    topic_aliases = {topic: aliases for topic, (_, aliases) in table.items()}

    grouped: dict[str, list[list[str]]] = {}
    for row in rows:
        grouped.setdefault(str(_row_dict(PAPERS_HEADERS, row)["week_start"]), []).append(list(row))

    history_topics = handler.read(TOPICS_SHEET)
    written = 0
    weeks: list[dict[str, Any]] = []
    for week_start in sorted(grouped, reverse=True):
        start = date.fromisoformat(week_start)
        week = WeekWindow(start, start + timedelta(days=7))
        existing = handler.read(PAPERS_SHEET, {"week_start": week_start})
        merged, fresh = _merge_rows(existing, grouped[week_start])
        if fresh:
            written += handler.upsert(PAPERS_SHEET, PAPERS_HEADERS, fresh, PAPERS_KEY)
        prev_rows = handler.read(PAPERS_SHEET, {"week_start": (start - timedelta(days=7)).isoformat()})
        finalized = week.end <= today
        topic_rows, dict_rows, overview = summarize_week(
            week=week, rows=merged, prev_rows=prev_rows, history_topics=history_topics,
            finalized=finalized, topic_packs=topic_packs)
        handler.upsert(TOPICS_SHEET, TOPICS_HEADERS, topic_rows, TOPICS_KEY)
        handler.upsert(OVERVIEW_SHEET, OVERVIEW_HEADERS, [overview], OVERVIEW_KEY)
        handler.upsert(DICT_SHEET, DICT_HEADERS, dict_rows, DICT_KEY)
        weeks.append({"week_start": week_start, "papers": len(merged),
                      "topics": len(topic_rows), "finalized": finalized})
    return {"ok": True, "written": written, "weeks": weeks}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m arxiv_digest.archive",
        description="长期热点档案：检查 Apps Script 端点是否可用。",
    )
    parser.add_argument("--health", action="store_true", help="向表格端点发一次健康检查")
    args = parser.parse_args(argv)
    from .config import load_config

    config = load_config()
    if not ready(config):
        print("ARCHIVE_WEBHOOK_URL / ARCHIVE_TOKEN 未配置：档案写入处于关闭状态。")
        return 2
    transport = HttpTransport(config.archive_webhook_url, config.archive_token,
                              config.archive_timeout, config.archive_max_rows)
    if args.health:
        try:
            payload = transport.health()
        except ArchiveError as exc:
            print(f"[FAIL] {exc}")
            return 1
        print(f"[ OK ] 表格端点可用：{payload}")
        return 0
    print("用 --health 检查端点；日常写入由每日投递成功后自动完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
