from __future__ import annotations

import json
import logging
import re
import urllib.request
from uuid import uuid4
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from .analysis import PROMPT_VERSION, analyze_papers
from .arxiv_client import ArxivRateLimitError, fetch_recent_papers
from .editorial import edition_for, edition_config, in_period, relevant_papers, select_news, news_counts, news_domain
from .calendar import CalendarBundle, empty_bundle, load_calendar
from .config import Config
from .contracts import DIGEST_VERSION, paper_to_dict
from .identity import canonical_id
from .storage import atomic_json, delivery_lock
from .cache import prune_cache
from .fulltext import DEFAULT_MAX_PAGES, fetch_fulltext, pdf_url_for
from .feedback import (
    apply_feedback,
    load_feedback,
    merge_csv,
    parse_feedback_csv,
    combine_stores, remember_papers, store_from_dict,
    save_feedback, FeedbackStore,
)
from .journals import fetch_journal_papers, load_journals, merge_with_arxiv
from .llm import generate_digest_content
from .models import Paper, ZoteroItem
from .packs import collect_categories, dispatch, load_packs, select_packs
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
    TrendRow, State, record_observations,
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
from .zotero_client import (
    fetch_zotero_items,
    library_match,
    preferences_from_items,
)
from .llm_client import LLMOptions, usage_scope, usage_snapshot

LOGGER = logging.getLogger(__name__)


def _assessment_packs(config: Config):
    packs = load_packs(config.packs_dir)
    selected = select_packs(packs, config.fields)
    return (*selected, *(pack for pack in packs if pack.is_generic and pack not in selected))


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
    pending_state: State | None = None
    pending_feedback: FeedbackStore | None = None
    report: dict = field(default_factory=dict)
    snapshot: dict | None = None

    @property
    def has_deliverable(self) -> bool:
        # Calendar/pulse are context, not a reason to repeat an otherwise empty daily mail.
        return bool(self.enriched or self.secondary or self.talks or (self.radar and not self.radar.is_empty))

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
            return OpenAI(api_key=config.openai_api_key, base_url=config.openai_base_url, timeout=config.llm_timeout, max_retries=2)
        return OpenAI(api_key=config.openai_api_key, timeout=config.llm_timeout, max_retries=2)
    except Exception as exc:
        LOGGER.warning("构造 OpenAI 客户端失败：%s，跳过两阶段分析。", exc)
        return None


def _fetch_fulltexts(config: Config, papers: list[Paper]) -> dict[str, str]:
    """为精读篇目抓全文。单篇失败返回空，由调用方回退摘要。"""
    if not config.fulltext_enabled or not papers:
        return {}
    texts: dict[str, str] = {}
    for paper in papers[: max(0, config.deep_papers)]:
        if paper.id.startswith("doi:"):
            continue
        url = pdf_url_for(paper.abs_url, paper.pdf_url)
        if not url:
            continue
        text = fetch_fulltext(
            url,
            max_chars=config.fulltext_max_chars,
            max_pages=config.fulltext_max_pages or DEFAULT_MAX_PAGES,
            timeout=config.fulltext_timeout,
            parse_timeout=config.fulltext_parse_timeout,
            cache_dir=config.cache_dir,
        )
        if text:
            texts[paper.id] = text
        else:
            LOGGER.info("全文抓取失败，该篇精读回退摘要：%s", paper.id)
    if texts:
        LOGGER.info("已获取 %d/%d 篇全文，用于精读。", len(texts), min(len(papers), config.deep_papers))
    return texts


def _maybe_analyze(
    config: Config, papers: list[Paper], *, library: Sequence[ZoteroItem] = ()
) -> list[Paper]:
    """两阶段分析：只在 ANALYSIS_ENABLED=true 时跑，任何异常都不影响发信。"""
    if not config.analysis_enabled or not papers or config.deep_papers <= 0:
        return papers
    client = _build_client(config)
    if client is None:
        return papers
    try:
        profile = load_profile(config.profile_path)
        packs = _assessment_packs(config)
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
            library=library if config.zotero_share_with_model else (),
            cache_dir=config.cache_dir,
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
        selected = select_packs(load_packs(config.packs_dir), config.fields)
        cadences = source_ids(config.sources_path, packs=selected)
        today = date.fromisoformat(day)
        skip = due_ids(state, cadences, today=today)
        if skip:
            LOGGER.info("按抓取频率跳过：%s（今天已经抓过）", "、".join(sorted(skip)))
        results = collect_sources(
            config.sources_path,
            fields=tuple(pack.id for pack in selected),
            packs=selected,
            max_items=config.sources_max_items,
            timeout=config.sources_timeout,
            skip_ids=skip,
        )
        mark_fetched(state, [result.key for result in results if result.ok], day=day)
    except Exception as exc:
        LOGGER.warning("多源抓取整体失败：%s", exc)
        return [], [], []

    secondary = filter_unseen(state, merge_items(results, kind=KIND_SECONDARY))
    secondary = select_news(secondary, today=today, timezone_name=config.digest_timezone,
                            target=config.world_news_target, maximum=config.world_news_max_items,
                            astronomy_percent=config.world_astronomy_percent,
                            max_age_days=config.world_news_max_age_days)
    talks = filter_unseen(state, merge_items(results, kind=KIND_TALK))

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
    if results and any(result.ok for result in results) and not secondary and not talks:
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
    materials_override=None,
) -> Pulse | None:
    """本周脉搏：先把材料数成主题命中表，再（有模型时）让模型在证据约束下写判断。

    顺序是刻意的——统计永远能跑，模型只是"加一层判断"。
    所以没 key、模型挂了、返回乱码，都只退到"只数数不判断"，不会让整封信失败。
    """

    if not config.pulse_enabled:
        return None
    materials = materials_override if materials_override is not None else materials_from(
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
    path_text: str, calendar: CalendarBundle, papers: list[Paper], summary: str,
    pulse: Pulse | None = None, *, secondary=(), talks=(), sources=(), radar=None,
    trend=(), report=None,
) -> dict | None:
    if not path_text:
        return None
    payload = {
        "version": DIGEST_VERSION, "prompt_version": PROMPT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary, "items": [paper_to_dict(paper) for paper in papers],
        "calendar_bundle": asdict(calendar),
        "pulse": asdict(pulse) if pulse else None,
        "radar": asdict(radar) if radar else None,
        "world": {"secondary": [asdict(item) for item in secondary],
                  "talk": [asdict(item) for item in talks],
                  "sources": [{"name": item.name, "error": item.error, "key": item.key} for item in sources],
                  "trend": [asdict(item) for item in trend]},
        "run": report or {},
    }
    try:
        _write_diagnostic(path_text, payload, str((report or {}).get("run_id") or ""), initial=True)
    except (OSError, RuntimeError):
        LOGGER.warning("本次快照写入失败；业务投递仍可继续。")
    return payload


def _write_diagnostic(path_text: str, payload: dict, run_id: str, *, initial: bool = False) -> None:
    """Keep an immutable run identity, and only finalize the matching latest pointer."""
    if not path_text:
        return
    path = Path(path_text)
    if not run_id:
        atomic_json(path, payload)
        return
    own_path = path.parent / "runs" / f"{path.stem}-{run_id}{path.suffix or '.json'}"
    atomic_json(own_path, payload)
    with delivery_lock(path):
        current = {}
        if path.is_file():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        owner_payload = current.get("run", current) if isinstance(current, dict) else {}
        owner = owner_payload.get("run_id") if isinstance(owner_payload, dict) else None
        if initial or owner == run_id:
            atomic_json(path, payload)


def _resolve_categories(config: Config) -> list[str]:
    """抓哪些分类：显式配了 ARXIV_CATEGORIES 就用它，否则由所选领域包取并集。

    这样"勾领域包"才是真的生效——不然用户改了 `FIELDS` 却发现抓来的还是
    astro-ph.GA/CO，领域包就只是个装饰。都没有时退回天文两分类。
    """

    if config.arxiv_categories:
        return list(config.arxiv_categories)
    try:
        packs = select_packs(load_packs(config.packs_dir), config.fields)
        categories = list(collect_categories(packs))
    except Exception as exc:
        LOGGER.warning("领域包读取失败（%s），退回默认分类。", exc)
        categories = []
    if categories:
        LOGGER.info("按领域包解析出抓取分类：%s", ", ".join(categories))
        return categories
    return ["astro-ph.GA", "astro-ph.CO"]


def _annotate_pack_reasons(config: Config, papers: list[Paper]) -> list[Paper]:
    """给每篇补一条"为什么它在"的真实理由。

    排序分只由关键词与 Zotero 相似度解释，分类匹配的贡献却是隐形的；
    于是没设关键词时整列显示一句英文占位符，读者误以为是判据。
    这里用领域包分派结果补上，让"按领域包匹配"真的可见。
    """

    try:
        packs = _assessment_packs(config)
    except Exception as exc:
        LOGGER.warning("领域包读取失败（%s），跳过匹配理由。", exc)
        return papers
    if not packs:
        return papers

    annotated: list[Paper] = []
    for paper in papers:
        pack = dispatch(packs, paper.categories, hint=paper.pack_hint, title=paper.title)
        label = "通用判断" if pack is None or pack.is_generic else f"领域包：{pack.name}"
        if label in paper.score_reasons:
            annotated.append(paper)
            continue
        annotated.append(replace(paper, score_reasons=[*paper.score_reasons, label]))
    return annotated


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
    categories = _resolve_categories(config)
    for days in candidates:
        used = days
        papers = fetch_recent_papers(
            categories,
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


def _fetch_weekly_window(config: Config, today: date) -> tuple[list[Paper], int]:
    """Exactly the last seven complete local days; never widen a weekly edition."""
    count = config.weekly_fetch_max_results
    papers = fetch_recent_papers(_resolve_categories(config), 7, max_results=count,
        page_size=config.arxiv_page_size, max_pages=(count + config.arxiv_page_size - 1) // config.arxiv_page_size,
        page_delay_seconds=config.arxiv_page_delay_seconds, timeout=config.arxiv_request_timeout,
        retries=config.arxiv_request_retries, timezone_name=config.digest_timezone, end_date=today,
        rate_limit_backoff_base=config.arxiv_rate_limit_backoff_base,
        rate_limit_backoff_max=config.arxiv_rate_limit_backoff_max,
        rate_limit_backoff_jitter=config.arxiv_rate_limit_backoff_jitter)
    return papers, 7


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


def _journal_topic_terms(config: Config) -> tuple[str, ...]:
    """Explicit YAML journal_terms, falling back to the selected pack's topic aliases."""
    terms: set[str] = set()
    for pack in select_packs(load_packs(config.packs_dir), config.fields):
        terms.update(pack.journal_terms or tuple(alias for _, aliases in pack.topics for alias in aliases))
    return tuple(sorted(term.strip().lower() for term in terms if term.strip()))


def _journal_matches(paper: Paper, terms: Sequence[str]) -> bool:
    if not terms:
        return True
    haystack = f"{paper.title} {paper.summary}".lower()
    for term in terms:
        # 词边界匹配：不能让 "mass" 命中 "massive"
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack):
            return True
    return False


def _merge_journals(config: Config, papers: list[Paper], window_days: int) -> list[Paper]:
    """把期刊论文并进 arXiv 结果。

    期刊源是"加餐"：Crossref 挂了就当没有，绝不能让整封信失败。
    窗口取 max(配置, arXiv 自适应窗口)，周末 arXiv 空窗时期刊仍可能有新文。
    """

    if not config.journals_enabled:
        return papers
    journals = load_journals(config.journals_path)
    all_packs = load_packs(config.packs_dir)
    selected_packs = select_packs(all_packs, config.fields)
    selected = {pack.id for pack in selected_packs}
    selected_categories = set(collect_categories(selected_packs))
    by_id = {pack.id: pack for pack in all_packs}
    journals = [journal for journal in journals if not journal.pack or journal.pack in selected
                or (journal.pack in by_id and selected_categories.intersection(by_id[journal.pack].categories))]
    if not journals:
        return papers
    lookback = max(config.journals_lookback_days, window_days)
    terms = _journal_topic_terms(config)
    try:
        journal_papers = fetch_journal_papers(
            journals,
            lookback_days=lookback,
            rows=config.journals_rows,
            max_per_journal=config.journals_max_per,
            predicate=lambda paper: _journal_matches(paper, terms),
            timeout=config.journals_timeout,
            mailto=config.journals_mailto,
        )
    except Exception as exc:  # noqa: BLE001 - 期刊源失败不致命
        LOGGER.warning("期刊源抓取失败（%s），本封只发 arXiv。", exc)
        return papers

    if not journal_papers:
        LOGGER.info("期刊源近 %d 天无新文（或 Crossref 无摘要）。", lookback)
        return papers

    kept = [paper for paper in journal_papers if _journal_matches(paper, terms)]
    if len(kept) != len(journal_papers):
        LOGGER.info(
            "期刊主题闸门：%d 篇里留 %d 篇（去掉 %d 篇与星系宇宙学无关的）",
            len(journal_papers),
            len(kept),
            len(journal_papers) - len(kept),
        )
    journal_papers = []
    for paper in kept:
        pack = dispatch(selected_packs, (), hint=paper.pack_hint, title=f"{paper.title} {paper.summary}")
        journal_papers.append(replace(paper, pack_hint=pack.id if pack else paper.pack_hint))

    merged = merge_with_arxiv(papers, journal_papers)
    added = len(merged) - len(papers)
    by_venue: dict[str, int] = {}
    for paper in merged:
        if paper.venue:
            by_venue[paper.venue] = by_venue.get(paper.venue, 0) + 1
    LOGGER.info(
        "期刊源：新增 %d 篇（%s）",
        added,
        "，".join(f"{venue} {count}" for venue, count in sorted(by_venue.items())) or "无",
    )
    return merged


def _load_library(config: Config) -> list[ZoteroItem]:
    """读 Zotero 库；没配 key 或读失败都返回空列表。"""

    if not config.zotero_api_key or not config.zotero_user_id:
        return []
    items = fetch_zotero_items(
        config.zotero_api_key,
        config.zotero_user_id,
        config.zotero_library_type,
        config.zotero_collection_id or "",
        limit=config.zotero_limit,
    )
    if items:
        LOGGER.info(
            "Zotero 库：%d 条（其中带标签 %d 条）",
            len(items),
            sum(1 for item in items if item.tags),
        )
    return items


def _fetch_sheet_csv(url: str, timeout: int) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "arxiv-astro-digest"})
    with urllib.request.urlopen(request, timeout=max(5, timeout)) as response:
        data = response.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            raise ValueError("反馈 CSV 超过 5 MiB")
        text = data.decode("utf-8", errors="replace")
        if text.lstrip("\ufeff \t\r\n").startswith("<"):
            raise ValueError("反馈端点返回 HTML；检查读取权限和凭据")
        return text


def _merge_feedback_sheet(config: Config, store):
    """把 Google Sheet 里的反馈并进本地 store。

    表格是"我在手机上点了按钮"的远端记录，本地 JSON 是"以前的操作"；
    两边都保留，同一篇同一维以表格为准（表格更可能是最近的决定）。
    """

    url = (config.feedback_sheet_csv_url or "").strip()
    if not url:
        return store
    try:
        text = _fetch_sheet_csv(url, config.feedback_sheet_timeout)
    except Exception as exc:  # noqa: BLE001 - 拉不到就只用本地记录
        LOGGER.warning("读取反馈表格失败（%s），仅用本地反馈。", type(exc).__name__)
        return store
    rows = parse_feedback_csv(text)
    if not rows:
        return store
    applied = merge_csv(store, rows)
    LOGGER.info("反馈表格：合并 %d 行", applied)
    return store


def commit_delivery(config: Config, result: PipelineResult, *, delivery: str = "committed") -> None:
    """Call only after SMTP success, or when no deliverable was produced."""
    if result.pending_state is not None:
        if delivery == "committed" and result.report.get("edition_key"):
            result.pending_state.editions[result.report["edition_key"]] = result.report["date"]
        if result.pending_feedback is not None:
            # state.json may be committed to a public fork: never publish personal votes.
            result.pending_state.feedback = {"version": 2, "votes": {}, "history": result.pending_feedback.history}
        if not save_state(result.pending_state, config.state_path):
            result.report.update(delivery="state_commit_failed", stage="state_commit",
                                 smtp_accepted=delivery == "committed")
            update_diagnostics(config, result)
            raise OSError("发送结果已确定，但状态保存失败；重跑前请检查状态，可能重复投递。")
        result.pending_state = None
        if result.pending_feedback is not None:
            try:
                with delivery_lock(config.feedback_path):
                    feedback = combine_stores(result.pending_feedback, load_feedback(config.feedback_path))
                    save_feedback(config.feedback_path, feedback)
                result.report["feedback_persisted"] = True
            except (OSError, RuntimeError):
                result.report["feedback_persisted"] = False
                LOGGER.warning("投递状态已保存，但本地反馈未能持久化；表格记录可在下次重新导入。")
        result.pending_feedback = None
    result.report["delivery"] = delivery
    update_diagnostics(config, result)


def update_diagnostics(config: Config, result: PipelineResult) -> None:
    try:
        run_id = str(result.report.get("run_id") or "")
        if config.run_report_path:
            _write_diagnostic(config.run_report_path, result.report, run_id)
        if config.digest_json_path and result.snapshot is not None:
            payload = dict(result.snapshot)
            payload["run"] = result.report
            _write_diagnostic(config.digest_json_path, payload, run_id)
    except (OSError, ValueError, RuntimeError):
        LOGGER.warning("投递和状态已确认，但诊断产物更新失败。")


def _remember_materials(state: State, papers, results, today: date, timezone_name: str = "UTC") -> list:
    from .pulse import Material
    astro_news = [item for item in merge_items(results, kind=KIND_SECONDARY) if news_domain(item) == "astronomy"]
    material_rows = materials_from(papers, astro_news,
                                   merge_items(results, kind=KIND_TALK), max_papers=len(papers), timezone_name=timezone_name)
    for material in material_rows:
        if material.date:
            material.text = material.text[:900]
            state.materials[canonical_id(material.id)] = asdict(material)
    cutoff = (today - timedelta(days=6)).isoformat()
    rows = [Material(**{name: raw[name] for name in Material.__dataclass_fields__ if name in raw})
            for raw in state.materials.values()
            if cutoff <= str(raw.get("date", ""))[:10] <= today.isoformat()]
    return sorted(rows, key=lambda row: (row.date, row.id), reverse=True)


def run_pipeline(config: Config, *, persist_state: bool = False, preview: bool = False) -> PipelineResult:
    with usage_scope():
        run_id = uuid4().hex
        started = datetime.now(timezone.utc)
        try:
            return _run_pipeline(config, persist_state=persist_state, preview=preview, run_id=run_id)
        except Exception as exc:
            failure = {"version": 1, "run_id": run_id, "date": _digest_today(config).isoformat(),
                       "timezone": config.digest_timezone, "started_at": started.isoformat(),
                       "delivery": "failed", "stage": "pipeline", "error_type": type(exc).__name__,
                       "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
                       "llm": usage_snapshot()}
            try:
                _write_diagnostic(config.run_report_path, failure, run_id, initial=True)
            except (OSError, RuntimeError):
                LOGGER.warning("失败诊断无法保存。")
            raise


def _run_pipeline(config: Config, *, persist_state: bool = False, preview: bool = False, run_id: str = "") -> PipelineResult:
    if persist_state:
        raise ValueError("run_pipeline 不确认投递；请在发信成功后调用 commit_delivery。")
    started = datetime.now(timezone.utc)
    today = _digest_today(config)
    edition = edition_for(config, today)
    config = edition_config(config, edition)
    day = today.isoformat()
    state = load_state(config.state_path)
    if edition.key and edition.key in state.editions and not preview:
        report = {"version": 1, "run_id": run_id, "date": day, "timezone": config.digest_timezone,
                  "edition": edition.kind, "edition_key": edition.key, "delivery": "skipped",
                  "skip_reason": "weekly_already_delivered"}
        try:
            _write_diagnostic(config.run_report_path, report, run_id, initial=True)
        except (OSError, RuntimeError):
            LOGGER.warning("跳过重复周报，但诊断不可写。")
        return PipelineResult([], [], [], "本期周报已投递", [], empty_bundle(), report=report)
    errors: list[str] = []
    primary_error = None
    prune_cache(config.cache_dir)
    calendar = (load_calendar(config.calendar_events_path, config.calendar_conferences_path,
                             today=today, horizon_days=config.calendar_horizon_days,
                             recent_days=config.calendar_recent_days)
                if config.calendar_enabled else empty_bundle())
    try:
        papers, window_days = (_fetch_weekly_window(config, today) if edition.kind == "weekly"
                               else _fetch_with_adaptive_window(config))
    except Exception as exc:
        LOGGER.warning("arXiv 主源失败，继续尝试期刊与二手来源：%s", type(exc).__name__)
        errors.append("arxiv:" + type(exc).__name__)
        primary_error = exc
        papers, window_days = [], config.lookback_days
    all_papers = _merge_journals(config, papers, window_days)
    if edition.kind == "weekly":
        all_papers = [paper for paper in all_papers if in_period(paper, edition, config.digest_timezone)]
    if config.trend_enabled:
        record_observations(state, papers, timezone_name=config.digest_timezone)
    library = _load_library(config)
    candidates = list(all_papers) if edition.kind == "weekly" else filter_unseen(state, all_papers)
    if config.zotero_skip_collected and library:
        candidates = [paper for paper in candidates if library_match(paper, library) is None]
    preferences = preferences_from_items(library)
    ranked_candidates = rank_papers(candidates, config.arxiv_keywords, preferences,
                                    max(len(candidates), config.arxiv_fetch_max_results), library=library)
    feedback = combine_stores(store_from_dict(state.feedback), load_feedback(config.feedback_path))
    remember_papers(feedback, all_papers)
    feedback = _merge_feedback_sheet(config, feedback)
    ranked_candidates = apply_feedback(feedback, ranked_candidates)
    if config.paper_relevance_enabled or edition.kind == "weekly":
        try:
            profile = load_profile(config.profile_path)
            ranked_candidates = relevant_papers(ranked_candidates, config.arxiv_keywords, _assessment_packs(config),
                profile, min_signals=config.weekly_min_signals if edition.kind == "weekly" else config.paper_min_signals,
                strict=edition.kind == "weekly")
        except (OSError, ValueError) as exc:
            LOGGER.warning("兴趣规则不可用：%s；本轮不以无关论文填充。", type(exc).__name__)
            ranked_candidates = []
    visible_limit = min(config.max_papers, config.deep_papers + config.brief_papers)
    ranked = filter_by_min_rating(ranked_candidates, config.min_rating)[:visible_limit]
    ranked = _annotate_pack_reasons(config, ranked)
    summary, enriched = generate_digest_content(
        config.openai_api_key, config.openai_model, ranked, max_llm_papers=config.max_llm_papers,
        max_abstract_chars=config.max_abstract_chars, base_url=config.openai_base_url,
        batch_size=config.llm_batch_size, thinking=config.llm_thinking, timeout=config.llm_timeout,
        edition_label="本周研究" if edition.kind == "weekly" else "今日", period_label=edition.period_label)
    enriched = _maybe_analyze(config, enriched, library=library)
    assessment_rejected = sum(p.analysis is not None and p.analysis.relevance_kind == "off" for p in enriched)
    if assessment_rejected:
        enriched = [p for p in enriched if p.analysis is None or p.analysis.relevance_kind != "off"]
        summary = f"本期保留 {len(enriched)} 篇兴趣匹配论文；另有 {assessment_rejected} 篇在精读评估中标为无关，已排除。请结合卡片中的原文依据阅读。"
    sources, secondary, talks = _collect_world(config, state, day)
    materials = _remember_materials(state, all_papers, sources, today, config.digest_timezone)
    materials = materials[:config.pulse_max_materials]
    pulse = _build_pulse(config, [], [], [], week_label=f"{today - timedelta(days=6)} 至 {today}（{config.digest_timezone}）",
                         materials_override=materials)
    # Do not repeat cross-listed papers already displayed in today's main section.
    radar = _build_radar(config, seen=(*state.seen, *(canonical_id(p.id) for p in enriched)))
    if primary_error and not all_papers and not secondary and not talks and not (radar and not radar.is_empty):
        if isinstance(primary_error, ArxivRateLimitError):
            raise primary_error
        raise RuntimeError("arXiv 主源失败，且其他来源没有可用内容；本次未提交状态。") from primary_error
    secondary = _translate_secondary(config, secondary)
    talks = _translate_secondary(config, talks)
    trend = weekly_trend(state, today=today, limit=config.trend_rows) if config.trend_enabled else []
    if edition.kind != "weekly":
        mark_seen(state, enriched, day=day)
    mark_seen(state, secondary, day=day)
    mark_seen(state, talks, day=day)
    if radar:
        mark_seen(state, radar.hits, day=day)
    # Keep metadata for votes, plus a bounded recent archive for delayed feedback.
    recent_ids = list(feedback.history)[-5000:]
    feedback.history = {key: value for key, value in feedback.history.items()
                        if key in feedback.votes or key in recent_ids}
    state.feedback = feedback.to_dict()
    prune(state, today=today)
    report = {
        "version": 1, "run_id": run_id, "date": day, "timezone": config.digest_timezone,
        "edition": edition.kind, "edition_key": edition.key, "period_start": str(edition.start),
        "period_end_exclusive": str(edition.end), "period_label": edition.period_label,
        "deep_papers": config.deep_papers, "brief_papers": config.brief_papers,
        "news_counts": news_counts(secondary), "news_target": config.world_news_target,
        "started_at": started.isoformat(), "completed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
        "window_days": window_days, "sampled_papers": len(all_papers),
        "unseen_candidates": len(candidates), "selected_papers": len(enriched),
        "analyzed_papers": sum(p.analysis is not None for p in enriched),
        "assessment_rejected": assessment_rejected,
        "secondary": len(secondary), "talks": len(talks), "pulse_materials": len(materials),
        "sources": [{"id": item.key, "ok": item.ok, "items": len(item.items)} for item in sources],
        "errors": errors, "delivery": "pending", "trend_basis": "sampled arXiv papers; not total submissions",
        "llm": usage_snapshot(),
    }
    snapshot = _write_digest_json(config.digest_json_path, calendar, enriched, summary, pulse,
                       secondary=secondary, talks=talks, sources=sources, radar=radar, trend=trend, report=report)
    if config.run_report_path:
        try:
            _write_diagnostic(config.run_report_path, report, run_id, initial=True)
        except (OSError, RuntimeError):
            LOGGER.warning("运行报告不可写，继续业务投递。")
    return PipelineResult(papers=all_papers, ranked_candidates=ranked_candidates, ranked=ranked,
                          summary=summary, enriched=enriched, calendar=calendar, secondary=secondary,
                          talks=talks, sources=sources, trend=trend, pulse=pulse, radar=radar,
                          window_days=window_days, pending_state=state, pending_feedback=feedback, report=report,
                          snapshot=snapshot)
