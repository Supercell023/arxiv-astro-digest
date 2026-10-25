from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from .analysis import PROMPT_VERSION, analyze_papers
from .arxiv_client import fetch_recent_papers
from .calendar import CalendarBundle, empty_bundle, load_calendar
from .config import Config
from .fulltext import DEFAULT_MAX_PAGES, fetch_fulltext, pdf_url_for
from .feedback import apply_feedback, load_feedback
from .llm import generate_digest_content
from .models import Paper
from .packs import load_packs, select_packs
from .profile import load_profile
from .pulse import Pulse, compose_pulse, count_topics, fallback_pulse, materials_from
from .radar import Radar, all_categories, annotate_radar, load_radar, select_hits
from .recommender import filter_by_min_rating, rank_papers
from .translate import translate_items
from .sources import (
    KIND_SECONDARY,
    KIND_TALK,
    Item,
    SourceResult,
    collect_sources,
    merge_items,
    source_ids,
)
from .state import (
    TrendRow,
    due_ids,
    filter_unseen,
    load_state,
    mark_fetched,
    mark_seen,
    prune,
    record_day,
    save_state,
    weekly_trend,
)
from .zotero_client import fetch_zotero_preferences
from .llm_client import LLMOptions

LOGGER = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    papers: list[Paper]
    ranked_candidates: list[Paper]
    ranked: list[Paper]
    summary: str
    enriched: list[Paper]
    calendar: CalendarBundle
    secondary: list[Item] = None  # type: ignore[assignment]
    talks: list[Item] = None  # type: ignore[assignment]
    trend: list[TrendRow] = None  # type: ignore[assignment]
    sources: list[SourceResult] = None  # type: ignore[assignment]
    pulse: Pulse | None = None
    radar: Radar | None = None
    window_days: int = 1

    def __post_init__(self) -> None:
        # dataclass 的默认值用 None 表示空列表，避免可变默认参数共享
        for name in ("secondary", "talks", "trend", "sources"):
            if getattr(self, name) is None:
                setattr(self, name, [])


def _build_client(config: Config):
    """只在真的要做两阶段分析时才构造客户端；失败就返回 None。"""
    if not config.openai_api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        LOGGER.warning("openai 未安装，跳过两阶段分析。")
        return None
    try:
        if config.openai_base_url:
            return OpenAI(api_key=config.openai_api_key, base_url=config.openai_base_url)
        return OpenAI(api_key=config.openai_api_key)
    except Exception as exc:
        LOGGER.warning("构造 OpenAI 客户端失败：%s，跳过两阶段分析。", exc)
        return None


def _fetch_fulltexts(config: Config, papers: list[Paper]) -> dict[str, str]:
    """为精读篇目抓全文。单篇失败返回空，由调用方回退摘要。"""
    if not config.fulltext_enabled or not papers:
        return {}
    texts: dict[str, str] = {}
    for paper in papers[: max(0, config.deep_papers)]:
        url = pdf_url_for(paper.abs_url, paper.pdf_url)
        if not url:
            continue
        text = fetch_fulltext(
            url,
            max_chars=config.fulltext_max_chars,
            max_pages=config.fulltext_max_pages or DEFAULT_MAX_PAGES,
            timeout=config.fulltext_timeout,
        )
        if text:
            texts[paper.id] = text
        else:
            LOGGER.info("全文抓取失败，该篇精读回退摘要：%s", paper.id)
    if texts:
        LOGGER.info("已获取 %d/%d 篇全文，用于精读。", len(texts), min(len(papers), config.deep_papers))
    return texts


def _maybe_analyze(config: Config, papers: list[Paper]) -> list[Paper]:
    """两阶段分析：只在 ANALYSIS_ENABLED=true 时跑，任何异常都不影响发信。"""
    if not config.analysis_enabled or not papers:
        return papers
    client = _build_client(config)
    if client is None:
        return papers
    try:
        profile = load_profile(config.profile_path)
        packs = select_packs(load_packs(config.packs_dir), config.fields)
        if profile.is_empty:
            LOGGER.warning("读者画像未读到（%s），两阶段分析仍会跑，但只会用内置兜底问题。", config.profile_path)
        if not packs:
            LOGGER.warning("没有可用的领域包（%s），所有论文都会落到 generic 判断。", config.packs_dir)
        fulltexts = _fetch_fulltexts(config, papers)
        return analyze_papers(
            client,
            papers,
            profile,
            packs,
            extract_model=config.analysis_extract_model,
            assess_model=config.analysis_assess_model,
            limit=config.deep_papers,
            fulltexts=fulltexts,
            extract_options=LLMOptions(
                temperature=1.0,
                max_tokens=6000,
                thinking=config.analysis_extract_thinking,
            ),
            assess_options=LLMOptions(
                temperature=1.0,
                max_tokens=9000,
                thinking=config.analysis_assess_thinking,
            ),
        )
    except Exception as exc:
        LOGGER.warning("两阶段分析整体失败：%s，退回单阶段摘要。", exc)
        return papers


def _collect_world(
    config: Config, state, day: str
) -> tuple[list[SourceResult], list[Item], list[Item]]:
    """抓二手解读与会议报告；已经推送过的条目不再出现。

    多源是"能坏"的：任何一个源挂掉都只影响它自己那一行，
    所以整段都包在 try 里。
    """

    if not config.sources_enabled:
        return [], [], []
    try:
        cadences = source_ids(config.sources_path)
        today = date.fromisoformat(day)
        skip = due_ids(state, cadences, today=today)
        if skip:
            LOGGER.info("按抓取频率跳过：%s（今天已经抓过）", "、".join(sorted(skip)))
        results = collect_sources(
            config.sources_path,
            fields=tuple(config.fields),
            max_items=config.sources_max_items,
            timeout=config.sources_timeout,
            skip_ids=skip,
        )
        mark_fetched(state, [result.key for result in results], day=day)
    except Exception as exc:
        LOGGER.warning("多源抓取整体失败：%s", exc)
        return [], [], []

    secondary = filter_unseen(state, merge_items(results, kind=KIND_SECONDARY))
    talks = filter_unseen(state, merge_items(results, kind=KIND_TALK))
    mark_seen(state, secondary, day=day)
    mark_seen(state, talks, day=day)

    if not results:
        LOGGER.warning(
            "「世界在动」没有内容：没有任何源被实际抓取。"
            "检查 SOURCES_ENABLED、config/sources.yaml 是否存在、FIELDS 是否把它们全过滤掉了。"
        )
    for result in results:
        if result.ok:
            LOGGER.info("源 %s：%d 条", result.name, len(result.items))
        else:
            LOGGER.info("源 %s 失败：%s", result.name, result.error)
    if results and not secondary and not talks:
        LOGGER.warning(
            "「世界在动」为空：%d 个源都拿到了内容，但全部已在 state.json 里推送过。"
            "想重看就往 state.json 里删掉对应的 seen/fetched 记录。",
            len(results),
        )
    return results, secondary, talks


def _build_pulse(
    config: Config,
    papers: list[Paper],
    secondary: list[Item],
    talks: list[Item],
    *,
    week_label: str = "",
) -> Pulse | None:
    """本周脉搏：先把材料数成主题命中表，再（有模型时）让模型在证据约束下写判断。

    顺序是刻意的——统计永远能跑，模型只是"加一层判断"。
    所以没 key、模型挂了、返回乱码，都只退到"只数数不判断"，不会让整封信失败。
    """

    if not config.pulse_enabled:
        return None
    materials = materials_from(
        papers, secondary, talks, max_papers=config.pulse_max_materials
    )
    if not materials:
        return None
    try:
        packs = select_packs(load_packs(config.packs_dir), config.fields)
    except Exception as exc:
        LOGGER.warning("脉搏读取领域包失败：%s，改用通用主题表。", exc)
        packs = []
    topic_table: list[tuple[str, tuple[str, ...]]] = []
    for pack in packs:
        topic_table.extend(pack.topics)
    topics = count_topics(materials, topic_table)
    if not topics:
        LOGGER.info("本周主题命中不足，跳过脉搏。")
        return None

    client = _build_client(config)
    if client is None:
        return fallback_pulse(materials, topics)
    try:
        profile = load_profile(config.profile_path)
    except Exception:
        profile = load_profile("")
    return compose_pulse(
        client,
        config.pulse_model,
        materials,
        topics,
        profile,
        week_label=week_label,
        limit=config.pulse_threads,
        options=LLMOptions(
            temperature=1.0,
            max_tokens=8000,
            thinking=config.pulse_thinking,
        ),
    )


def _primary_categories(papers: list[Paper]) -> list[str]:
    """只取主分类——cross-list 会把投稿量统计放大两三倍。"""

    return [paper.categories[0] for paper in papers if paper.categories]


def _build_radar(config: Config, *, seen: Sequence[str] = ()) -> Radar | None:
    """邻域雷达：只挑不读，给几行"隔壁在动什么"。

    故意不喂给精读/评论链路——那是给自己方向的预算，不该花在别的领域。
    """

    if not config.radar_enabled:
        return None
    try:
        neighborhoods = load_radar(config.radar_path, fallback_dir=_package_root())
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("邻域雷达配置读取失败：%s", exc)
        return None
    if not neighborhoods:
        LOGGER.info("没有配置邻域雷达，跳过。")
        return None

    neighborhoods = [
        replace(item, max_items=max(1, config.radar_per_field)) for item in neighborhoods
    ]
    categories = all_categories(neighborhoods)
    if not categories:
        return None

    try:
        papers = fetch_recent_papers(
            categories,
            lookback_days=max(1, config.radar_lookback_days),
            max_results=max(20, len(categories) * 10),
            page_size=50,
            max_pages=2,
            page_delay_seconds=config.arxiv_page_delay_seconds,
            timeout=config.arxiv_request_timeout,
            retries=config.arxiv_request_retries,
            timezone_name=config.digest_timezone,
            rate_limit_backoff_base=config.arxiv_rate_limit_backoff_base,
            rate_limit_backoff_max=config.arxiv_rate_limit_backoff_max,
            rate_limit_backoff_jitter=config.arxiv_rate_limit_backoff_jitter,
        )
    except Exception as exc:  # noqa: BLE001 - 雷达失败不影响主流程
        LOGGER.warning("邻域雷达抓取失败：%s", exc)
        return None

    radar = select_hits(
        neighborhoods,
        papers,
        max_fields=max(1, config.radar_max_fields),
        per_field=max(1, config.radar_per_field),
        skip_ids=seen,
    )
    if radar.is_empty:
        return None

    client = _build_client(config)
    if client is None:
        return radar
    return annotate_radar(
        client,
        config.pulse_model,
        radar,
        options=LLMOptions(
            temperature=0.3,
            max_tokens=4000,
            thinking=config.radar_thinking,
        ),
    )


def _package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _translate_secondary(config: Config, items: list[Item]) -> list[Item]:
    """把二手解读/会议报告译成中文；没有 key 或关闭时原样返回。"""

    if not items or not config.translate_enabled:
        return items
    client = _build_client(config)
    if client is None:
        return items
    return translate_items(
        client,
        config.pulse_model,
        items,
        limit=config.translate_limit,
        options=LLMOptions(
            temperature=0.3,
            max_tokens=3000,
            thinking=config.translate_thinking,
        ),
    )


def _write_digest_json(
    path_text: str,
    calendar: CalendarBundle,
    papers: list[Paper],
    summary: str,
    pulse: Pulse | None = None,
) -> None:
    """把这一期落盘，方便回放与调参；格式变更时改 version。"""
    if not path_text:
        return
    payload = {
        "version": 1,
        "prompt_version": PROMPT_VERSION,
        "summary": summary,
        "pulse": None
        if pulse is None
        else {
            "headline": pulse.headline,
            "threads": [
                {
                    "question": thread.question,
                    "summary": thread.summary,
                    "status": thread.status,
                    "evidence": list(thread.evidence),
                    "why_you": thread.why_you,
                }
                for thread in pulse.threads
            ],
            "topics": [
                {"label": topic.label, "hits": topic.hits, "sources": list(topic.sources)}
                for topic in pulse.topics
            ],
            "quiet": pulse.quiet,
            "basis": pulse.basis,
            "materials": pulse.materials,
            "source_count": pulse.source_count,
        },
        "calendar": [
            {
                "title": line.title,
                "when": line.when_label,
                "countdown": line.countdown_label,
                "kind": line.kind_label,
                "url": line.url,
            }
            for line in calendar.rows
        ],
        "items": [
            {
                "id": paper.id,
                "title": paper.title,
                "categories": paper.categories,
                "score": paper.score,
                "rating": paper.rating,
                "chinese_summary": paper.chinese_summary,
                "analysis": None
                if paper.analysis is None
                else {
                    "relevance": paper.analysis.relevance,
                    "relevance_kind": paper.analysis.relevance_kind,
                    "relevance_reason": paper.analysis.relevance_reason,
                    "data": paper.analysis.data,
                    "processing": paper.analysis.processing,
                    "systematics": paper.analysis.systematics,
                    "method": paper.analysis.method,
                    "result": paper.analysis.result,
                    "caveat": paper.analysis.caveat,
                    "for_you": paper.analysis.for_you,
                    "confidence": paper.analysis.confidence,
                    "evidence": list(paper.analysis.evidence),
                    "basis": paper.analysis.basis,
                    "prompt_version": paper.analysis.prompt_version,
                },
            }
            for paper in papers
        ],
    }
    try:
        path = Path(path_text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("已写入 %s", path)
    except Exception as exc:
        LOGGER.warning("写入 digest JSON 失败（%s）：%s", path_text, exc)


def _fetch_with_adaptive_window(config: Config) -> tuple[list[Paper], int]:
    """回看窗口自适应。

    arXiv 只在工作日（美东 20:00）公告，周末与节假日严格按 1 天窗口去抓，
    得到的就是一封空简报。这里从配置值起步，篇数不够就往后放宽到 14 天，
    并把真实用到的天数带回去，好在邮件头部说明"覆盖近 N 天"。
    """

    target = max(1, config.max_papers)
    configured = max(1, config.lookback_days)
    candidates = [configured] + [d for d in (2, 3, 5, 7, 10, 14) if d > configured]

    papers: list[Paper] = []
    used = configured
    for days in candidates:
        used = days
        papers = fetch_recent_papers(
            config.arxiv_categories,
            days,
            max_results=config.arxiv_fetch_max_results,
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
        if len(papers) >= target:
            break

    if used != configured:
        LOGGER.info(
            "回看窗口自适应：配置 %d 天 → 实际 %d 天（%d 篇；周末/节假日 arXiv 不公告）",
            configured,
            used,
            len(papers),
        )
    return papers, used


def _digest_today(config: Config) -> date:
    """"今天"按 DIGEST_TIMEZONE 算，不按运行机器的时区。

    GitHub Actions 跑在 UTC：北京时间 01:22 触发时，UTC 还是前一天
    17:22。若按系统时区取日期，就会和昨天的 state.json 撞成同一天，
    导致所有"每天抓一次"的源被当成"今天已经抓过"而整体跳过。
    """

    try:
        return datetime.now(ZoneInfo(config.digest_timezone)).date()
    except Exception:  # 时区名写错时不致命，退回系统本地时间
        LOGGER.warning("时区 %r 无法识别，改用系统本地时间。", config.digest_timezone)
        return datetime.now().date()


def run_pipeline(config: Config, *, persist_state: bool = True) -> PipelineResult:
    today = _digest_today(config)
    day = today.isoformat()
    state = load_state(config.state_path)

    # 日历只读本地文件，先加载：即使后续 arXiv/OpenAI 全部失败，它也能独立成立。
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
    papers, window_days = _fetch_with_adaptive_window(config)
    preferences = fetch_zotero_preferences(        config.zotero_api_key,
        config.zotero_user_id,
        config.zotero_library_type,
        config.zotero_collection_id,
    )
    ranked_candidates = rank_papers(papers, config.arxiv_keywords, preferences, config.arxiv_fetch_max_results)
    feedback = load_feedback(config.feedback_path)
    if not feedback.is_empty:
        ranked_candidates = apply_feedback(feedback, ranked_candidates)
        LOGGER.info("已应用反馈偏好：%d 条记录", len(feedback.votes))
    ranked = filter_by_min_rating(ranked_candidates, config.min_rating)[: config.max_papers]
    summary, enriched = generate_digest_content(
        config.openai_api_key,
        config.openai_model,
        ranked,
        max_llm_papers=config.max_llm_papers,
        max_abstract_chars=config.max_abstract_chars,
        base_url=config.openai_base_url,
        batch_size=config.llm_batch_size,
        thinking=config.llm_thinking,
    )
    enriched = _maybe_analyze(config, enriched)

    # 二手解读 / 会议报告：独立于 arXiv，抓不到就只是少一段。
    sources, secondary, talks = _collect_world(config, state, day)

    week_label = f"{today}（北京时间）"
    pulse = _build_pulse(config, enriched, secondary, talks, week_label=week_label)
    radar = _build_radar(config, seen=tuple(state.seen))
    secondary = _translate_secondary(config, secondary)
    talks = _translate_secondary(config, talks)

    trend: list[TrendRow] = []
    if config.trend_enabled:
        record_day(state, day=day, categories=_primary_categories(papers))
        trend = weekly_trend(state, today=today, limit=config.trend_rows)

    mark_seen(state, papers, day=day)
    prune(state, today=today)
    if persist_state:
        save_state(state, config.state_path)

    _write_digest_json(config.digest_json_path, calendar, enriched, summary, pulse)
    return PipelineResult(
        papers=papers,
        ranked_candidates=ranked_candidates,
        ranked=ranked,
        summary=summary,
        enriched=enriched,
        calendar=calendar,
        secondary=secondary,
        talks=talks,
        trend=trend,
        sources=sources,
        pulse=pulse,
        radar=radar,
        window_days=window_days,
    )
