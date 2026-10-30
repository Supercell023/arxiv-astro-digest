"""生成 HTML 预览样张（不联网发信、不需要 SMTP）。

两种用法：

    python scripts/build_preview.py                 # 用 config/preview_replay.json 渲染
    python scripts/build_preview.py --discover 6    # 抓最近的真实论文，生成 replay 骨架

为什么要 replay 文件：预览必须能离线、可重复地渲染同一份内容。
真实运行时内容是模型现生成的；预览时用录制文件，改动模板后可以立刻看效果，
不必每次都花钱调模型。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from arxiv_digest.calendar import empty_bundle, load_calendar  # noqa: E402
from arxiv_digest.config import load_config
from arxiv_digest.contracts import paper_from_dict, papers_from_payload  # noqa: E402
from arxiv_digest.emailer import render_email_html  # noqa: E402

THEME = "dark"  # main() 里由 --theme 覆盖
from arxiv_digest.fulltext import extract_figure_captions, fetch_fulltext, pdf_url_for  # noqa: E402
from arxiv_digest.models import Analysis, Paper, ScoreBreakdown  # noqa: E402
from arxiv_digest.pulse import pulse_from_payload  # noqa: E402

DEFAULT_REPLAY = REPO_ROOT / "config" / "preview_replay.json"
DEFAULT_OUT = REPO_ROOT / "preview.html"


def _parse_time(value: str) -> datetime:
    text = (value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_papers(spec: dict, *, allow_network: bool = False) -> tuple[list[Paper], list[str]]:
    """Replay is offline by default; downloads never upgrade recorded analysis provenance."""
    papers = papers_from_payload(spec)
    notes: list[str] = []
    if allow_network:
        from dataclasses import replace
        raw_items = spec.get("items", spec.get("papers", []))
        for index, (paper, raw) in enumerate(zip(papers, raw_items)):
            if not raw.get("fetch_fulltext"):
                continue
            text = fetch_fulltext(pdf_url_for(paper.abs_url, paper.pdf_url),
                                  max_chars=int(spec.get("fulltext_max_chars") or 0))
            notes.append(f"{paper.id}: PDF {'已获取' if text else '失败，保留录制内容'}")
            if text and paper.analysis:
                papers[index] = replace(paper, analysis=replace(paper.analysis, figures=extract_figure_captions(text)))
    return papers, notes


def _world_from_spec(spec: dict):
    """把 replay 里的 world 区块还原成渲染需要的四样东西。"""

    from arxiv_digest.sources import Item, SourceResult
    from arxiv_digest.state import TrendRow

    block = spec.get("world") or {}

    def items(kind: str) -> list:
        return [
            Item(
                id=raw.get("id", ""),
                kind=kind,
                title=raw.get("title", ""),
                url=raw.get("url", ""),
                source=raw.get("source", ""),
                published=raw.get("published", ""),
                text=raw.get("text", ""),
                tags=tuple(raw.get("tags") or ()),
                meta=dict(raw.get("meta") or {}),
                title_zh=raw.get("title_zh", ""),
                text_zh=raw.get("text_zh", ""),
            )
            for raw in (block.get(kind) or [])
        ]

    trend = [TrendRow(label=str(raw.get("label", "")), this_week=int(raw.get("this_week") or 0), last_week=int(raw.get("last_week") or 0)) for raw in (block.get("trend") or [])]
    sources = [
        SourceResult(name=str(raw.get("name", "")), items=[], error=str(raw.get("error", "")))
        for raw in (block.get("sources") or [])
    ]
    return items("secondary"), items("talk"), trend, sources


def _radar_from_spec(spec: dict):
    """把 replay 里的 radar 区块还原成 Radar（v2：按方向分块，只展第一条）。"""

    from arxiv_digest.radar import Radar, RadarBlock, RadarHit, _RULE_HINTS, _signal_score

    block = spec.get("radar") or {}

    def _hit(raw: dict) -> RadarHit:
        summary = raw.get("summary", "")
        why = raw.get("why", "")
        return RadarHit(
            neighborhood=raw.get("neighborhood", ""),
            title=raw.get("title", ""),
            url=raw.get("url", ""),
            category=raw.get("category", ""),
            published=raw.get("published", ""),
            why=why,
            reason=raw.get("reason", "") or why,
            summary=summary,
            abstract=raw.get("abstract", ""),
            authors=tuple(raw.get("authors") or ()),
            id=raw.get("id", ""),
        )

    raw_blocks = block.get("blocks")
    if raw_blocks:
        blocks = []
        for raw in raw_blocks:
            headline_raw = raw.get("headline") or {}
            rest = tuple(_hit(item) for item in (raw.get("rest") or []))
            blocks.append(
                RadarBlock(
                    neighborhood=raw.get("neighborhood", ""),
                    headline=_hit(headline_raw),
                    rest=rest,
                    total=int(raw.get("total") or (1 + len(rest))),
                )
            )
    else:
        # 旧结构：一串 hits，按 neighborhood 分组，第一条当 headline
        grouped: dict[str, list[RadarHit]] = {}
        for raw in (block.get("hits") or []):
            grouped.setdefault(raw.get("neighborhood", ""), []).append(_hit(raw))
        blocks = [RadarBlock(neighborhood=name, headline=items[0], rest=tuple(items[1:]), total=len(items)) for name, items in grouped.items()]

    counts = tuple(
        (str(item[0]), int(item[1])) for item in (block.get("counts") or []) if len(item) == 2
    )
    return Radar(
        blocks=tuple(blocks),
        counts=counts,
        basis=block.get("basis", "rule"),
        quiet=block.get("quiet", ""),
    )

def render(spec_path: Path, out_path: Path, *, deep: int | None = None, brief: int | None = None, allow_network: bool = False) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    deep = deep if deep is not None else (spec.get("run") or {}).get("deep_papers", 4)
    brief = brief if brief is not None else (spec.get("run") or {}).get("brief_papers", 10)
    papers, notes = build_papers(spec, allow_network=allow_network)
    if not papers:
        if not spec.get("world") and not spec.get("calendar_bundle"):
            print(f"[FAIL] {spec_path} 里没有可回放内容")
            return 1

    config = load_config()
    calendar = (
        load_calendar(
            config.calendar_events_path,
            config.calendar_conferences_path,
            horizon_days=config.calendar_horizon_days,
            recent_days=config.calendar_recent_days,
        )
        if config.calendar_enabled
        else empty_bundle()
    )
    if isinstance(spec.get("calendar_bundle"), dict):
        from arxiv_digest.calendar import CalendarBundle, CalendarLine
        raw_calendar = spec["calendar_bundle"]
        calendar = CalendarBundle(**{key: (CalendarLine(**raw_calendar[key]) if raw_calendar.get(key) else None)
                                     if key == "next_far" else tuple(CalendarLine(**raw) for raw in raw_calendar.get(key, []))
                                     for key in CalendarBundle.__dataclass_fields__})
    secondary, talks, trend, sources = _world_from_spec(spec)
    pulse = pulse_from_payload(spec.get("pulse"))
    radar = _radar_from_spec(spec)
    html = render_email_html(
        spec.get("summary") or "",
        papers,
        calendar,
        timezone_name=config.digest_timezone,
        deep_papers=deep,
        brief_papers=brief,
        edition=(spec.get("run") or {}).get("edition", "daily"),
        period_label=(spec.get("run") or {}).get("period_label", ""),
        news_counts=(spec.get("run") or {}).get("news_counts", {}),
        secondary=secondary,
        talks=talks,
        trend=trend,
        sources=sources,
        pulse=pulse,
        radar=radar,
        feedback_url=spec.get("feedback_url") or "",
        feedback_signing_key=config.feedback_signing_key,
        theme=THEME,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    for note in notes:
        print("  " + note)
    print(f"[ OK ] 已写出 {out_path}（{len(html):,} 字符，{len(papers)} 篇）")
    analyzed = sum(1 for paper in papers if paper.analysis is not None)
    fulltext = sum(1 for paper in papers if paper.analysis and paper.analysis.basis == "fulltext")
    print(f"       其中 {analyzed} 篇带分析，{fulltext} 篇基于全文")
    return 0


def discover(count: int, out_path: Path, lookback: int) -> int:
    """抓最近的真实论文，写出 replay 骨架（摘要留空，等人工/模型补）。"""
    from arxiv_digest.arxiv_client import fetch_recent_papers
    from arxiv_digest.pipeline import _resolve_categories

    config = load_config()
    papers = fetch_recent_papers(
        _resolve_categories(config),
        lookback,
        max_results=200,
        page_size=config.arxiv_page_size,
        max_pages=config.arxiv_max_pages,
        page_delay_seconds=config.arxiv_page_delay_seconds,
        timeout=config.arxiv_request_timeout,
        retries=config.arxiv_request_retries,
        timezone_name=config.digest_timezone,
        rate_limit_backoff_base=config.arxiv_rate_limit_backoff_base,
        rate_limit_backoff_max=config.arxiv_rate_limit_backoff_max,
        rate_limit_backoff_jitter=config.arxiv_rate_limit_backoff_jitter,
    )
    picked = papers[:count]
    payload = {
        "summary": "预览样张：以下条目来自 arXiv 真实列表。",
        "papers": [
            {
                "id": paper.id,
                "title": paper.title,
                "authors": paper.authors[:6],
                "categories": paper.categories,
                "published": paper.published.isoformat(),
                "abs_url": paper.abs_url,
                "pdf_url": paper.pdf_url,
                "summary": paper.summary,
                "score": paper.score,
                "rating": paper.rating,
                "fetch_fulltext": index < 4,
                "summary_zh": "",
                "analysis": None,
            }
            for index, paper in enumerate(picked)
        ],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ OK ] 已写出骨架 {out_path}（{len(picked)} 篇，共抓到 {len(papers)} 篇）")
    for paper in picked:
        print(f"\n--- {paper.id} | {paper.score:.2f} | {', '.join(paper.categories)}")
        print(paper.title)
        print(paper.summary[:700])
    return 0


def refresh_world(spec_path: Path) -> int:
    """现场抓一次二手源，把结果写进 replay 的 world 区块，之后可离线重渲。"""
    from arxiv_digest.sources import collect_sources, merge_items

    config = load_config()
    results = collect_sources(
        config.sources_path,
        fields=tuple(config.fields),
        max_items=config.sources_max_items,
        timeout=config.sources_timeout,
    )
    secondary = merge_items(results, kind="secondary")[:6]
    talks = merge_items(results, kind="talk")[:6]

    def dump(item) -> dict[str, object]:
        return {
            "id": item.id,
            "title": item.title,
            "url": item.url,
            "source": item.source,
            "published": item.published,
            "text": item.text,
            "tags": list(item.tags),
            "meta": item.meta,
        }

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["world"] = {
        "sources": [{"name": result.name, "error": result.error} for result in results],
        "secondary": [dump(item) for item in secondary],
        "talk": [dump(item) for item in talks],
        "trend": (spec.get("world") or {}).get("trend") or [],
    }
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    for result in results:
        print(f"  {result.name}: {result.status_label}")
    print(f"[ OK ] 已更新 {spec_path} 的 world 区块（二手 {len(secondary)} 条 / 报告 {len(talks)} 条）")
    return 0


def main(argv: list[str] | None = None) -> int:
    global THEME
    parser = argparse.ArgumentParser(description="生成 HTML 预览样张")
    parser.add_argument("--replay", default=str(DEFAULT_REPLAY), help="录制文件路径")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 HTML 路径")
    parser.add_argument("--deep", type=int, help="覆盖录制数据的精读篇数，旧样张默认 4")
    parser.add_argument("--brief", type=int, help="覆盖录制数据的速览篇数，旧样张默认 10")
    parser.add_argument("--discover", type=int, default=0, help="抓 N 篇真实论文生成骨架")
    parser.add_argument("--lookback", type=int, default=7, help="discover 时回看天数")
    parser.add_argument("--fetch-fulltext", action="store_true", help="允许联网下载样张指定的 PDF（不调用模型）")
    parser.add_argument("--refresh-world", action="store_true", help="现场抓一次二手源写进 replay")
    parser.add_argument("--theme", default="dark", choices=("dark", "light"), help="邮件主题：dark 深空 / light 暖纸")
    args = parser.parse_args(argv)

    replay = Path(args.replay)
    out = Path(args.out)
    if args.discover:
        return discover(args.discover, replay, args.lookback)
    if not replay.exists():
        print(f"[FAIL] 找不到 {replay}；先跑 --discover 生成骨架")
        return 1
    if args.refresh_world:
        refresh_world(replay)
    THEME = args.theme
    return render(replay, out, deep=args.deep, brief=args.brief, allow_network=args.fetch_fulltext)


if __name__ == "__main__":
    raise SystemExit(main())
