from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env(name: str, default: str) -> str:
    return os.getenv(name) or default


def _env_model(name: str, default: str) -> str:
    """读模型名，顺手清掉两种最常见的手写错误。

    python-dotenv 只在值非空时才把 `#` 之后当注释砍掉：
    `PULSE_MODEL=   # 留空则用 OPENAI_MODEL` 会被原样读成
    `'# 留空则用 OPENAI_MODEL'`，然后作为 model 名发给端点 → 400。
    这里统一兜住：行内注释截断、引号剥掉、空值回落到默认。
    """

    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.split("#", 1)[0].strip().strip("'\"")
    return value or default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {value!r}.") from exc


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    cleaned = (value or "").split("#", 1)[0].strip().strip("'\"").lower()
    if not cleaned:
        return default
    if cleaned in {"1", "true", "yes", "y", "on", "enabled", "enable"}:
        return True
    if cleaned in {"0", "false", "no", "n", "off", "disabled", "disable"}:
        return False
    logging.getLogger(__name__).warning("%s 的开关值无法识别，按旧版语义关闭；建议显式使用 true/false。", name)
    return False


def _env_tristate(name: str, default: bool | None = None) -> bool | None:
    """三态开关：变量不存在 = default（None = 不要把这个参数发给端点）。"""

    value = os.getenv(name)
    cleaned = (value or "").split("#", 1)[0].strip().strip("'\"").lower()
    if not cleaned or cleaned in {"auto", "default", "none"}:
        return default
    return _env_bool(name, False)


def _default_thinking() -> bool | None:
    """DeepSeek 默认开思考（费 token 又慢），结构化任务默认关；其他端点不发这个参数。"""

    base = (os.getenv("OPENAI_BASE_URL") or "").lower()
    return False if "deepseek" in base else None


def _default_model() -> str:
    """没显式指定模型时按端点猜一个默认值。

    用户配了 DeepSeek 的 base_url 却忘了写 OPENAI_MODEL 时，发 `gpt-4o-mini`
    过去会直接报错——这里按端点名兜底，少一个配置陷阱。
    """

    explicit = _env_model("OPENAI_MODEL", "")
    if explicit:
        return explicit
    base = (os.getenv("OPENAI_BASE_URL") or "").lower()
    if "deepseek" in base:
        return "deepseek-flash"
    return "gpt-4o-mini"


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


@dataclass(frozen=True)
class Config:
    arxiv_categories: list[str]
    arxiv_keywords: list[str]
    digest_timezone: str
    arxiv_fetch_max_results: int
    arxiv_page_size: int
    arxiv_max_pages: int
    arxiv_page_delay_seconds: int
    arxiv_request_timeout: int
    arxiv_request_retries: int
    arxiv_rate_limit_backoff_base: int
    arxiv_rate_limit_backoff_max: int
    arxiv_rate_limit_backoff_jitter: int
    arxiv_rate_limit_graceful: bool
    max_papers: int
    max_llm_papers: int
    max_abstract_chars: int
    llm_batch_size: int
    min_rating: int
    lookback_days: int
    deep_papers: int
    brief_papers: int
    calendar_enabled: bool
    calendar_events_path: str
    calendar_conferences_path: str
    calendar_horizon_days: int
    calendar_recent_days: int
    openai_api_key: str | None = field(repr=False)
    openai_base_url: str | None
    openai_model: str
    analysis_enabled: bool
    analysis_extract_model: str
    analysis_assess_model: str
    analysis_extract_thinking: bool | None
    analysis_assess_thinking: bool | None
    llm_thinking: bool | None
    digest_json_path: str
    profile_path: str
    packs_dir: str
    fields: list[str]
    fulltext_enabled: bool
    fulltext_max_chars: int
    fulltext_max_pages: int
    fulltext_timeout: int
    journals_enabled: bool
    journals_path: str
    journals_rows: int
    journals_max_per: int
    journals_timeout: int
    journals_lookback_days: int
    journals_mailto: str
    preprint_enabled: bool
    preprint_timeout: int
    preprint_max_results: int
    preprint_abstract_lookups: int
    sources_enabled: bool
    sources_path: str
    sources_max_items: int
    sources_timeout: int
    state_path: str
    trend_enabled: bool
    trend_rows: int
    pulse_enabled: bool
    pulse_model: str
    pulse_threads: int
    pulse_max_materials: int
    pulse_thinking: bool | None
    radar_enabled: bool
    radar_path: str
    radar_per_field: int
    radar_max_fields: int
    radar_lookback_days: int
    email_theme: str
    email_attach_html: bool
    translate_enabled: bool
    translate_thinking: bool | None
    translate_limit: int
    radar_thinking: bool | None
    feedback_url: str
    feedback_path: str
    feedback_sheet_csv_url: str
    feedback_sheet_timeout: int
    zotero_api_key: str | None = field(repr=False)
    zotero_user_id: str | None
    zotero_library_type: str
    zotero_collection_id: str | None
    zotero_limit: int
    zotero_skip_collected: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str = field(repr=False)
    email_from: str
    email_to: str
    smtp_timeout: int = 30
    cache_dir: str = ".cache/arxiv-digest"
    run_report_path: str = "run_report.json"
    llm_timeout: int = 90
    fulltext_parse_timeout: int = 30
    zotero_share_with_model: bool = False
    feedback_signing_key: str = field(default="", repr=False)
    digest_edition: str = "auto"
    weekly_enabled: bool = True
    weekly_weekday: int = 6
    weekly_max_papers: int = 12
    weekly_deep_papers: int = 6
    weekly_fetch_max_results: int = 600
    weekly_min_signals: int = 2
    paper_relevance_enabled: bool = True
    paper_min_signals: int = 1
    world_news_target: int = 30
    world_news_max_items: int = 36
    world_astronomy_percent: int = 50
    world_news_max_age_days: int = 7

    def __post_init__(self) -> None:
        positive = ("arxiv_fetch_max_results", "arxiv_page_size", "arxiv_max_pages", "arxiv_request_timeout",
                    "arxiv_request_retries", "max_papers", "llm_batch_size", "lookback_days", "smtp_timeout",
                    "llm_timeout", "fulltext_timeout", "fulltext_parse_timeout", "fulltext_max_chars", "fulltext_max_pages",
                    "journals_rows", "journals_max_per", "journals_timeout", "sources_timeout", "sources_max_items", "zotero_limit",
                    "preprint_timeout", "preprint_max_results")
        for name in positive:
            if getattr(self, name) < 1:
                raise ValueError(f"{name} 必须 >= 1")
        if self.preprint_abstract_lookups < 0:
            raise ValueError("PREPRINT_ABSTRACT_LOOKUPS 必须 >= 0")
        if self.deep_papers < 0 or self.brief_papers < 0 or self.deep_papers + self.brief_papers < 1:
            raise ValueError("DEEP_PAPERS / BRIEF_PAPERS 必须非负，且至少展示一篇")
        if not 1 <= self.min_rating <= 5:
            raise ValueError("MIN_RATING 必须在 1 到 5 之间")
        if not 1 <= self.smtp_port <= 65535:
            raise ValueError("SMTP_PORT 必须在 1 到 65535 之间")
        if self.digest_edition not in {"auto", "daily", "weekly"}:
            raise ValueError("DIGEST_EDITION 必须是 auto / daily / weekly")
        if not 0 <= self.weekly_weekday <= 6:
            raise ValueError("WEEKLY_WEEKDAY 必须在 0（周一）到 6（周日）之间")
        for name in ("weekly_max_papers", "weekly_fetch_max_results", "weekly_min_signals",
                     "paper_min_signals", "world_news_target", "world_news_max_items", "world_news_max_age_days"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} 必须 >= 1")
        if not 0 <= self.weekly_deep_papers <= self.weekly_max_papers:
            raise ValueError("WEEKLY_DEEP_PAPERS 必须在 0 到 WEEKLY_MAX_PAPERS 之间")
        if not 0 <= self.world_astronomy_percent <= 100:
            raise ValueError("WORLD_ASTRONOMY_PERCENT 必须在 0 到 100 之间")
        if self.world_news_max_items < self.world_news_target:
            raise ValueError("WORLD_NEWS_MAX_ITEMS 不得小于 WORLD_NEWS_TARGET")
        from zoneinfo import ZoneInfo
        ZoneInfo(self.digest_timezone)


def load_config() -> Config:
    _load_dotenv_if_available()

    return Config(
        arxiv_categories=_split_csv(_env("ARXIV_CATEGORIES", "")),
        arxiv_keywords=_split_csv(_env("ARXIV_KEYWORDS", "")),
        digest_timezone=_env("DIGEST_TIMEZONE", "Asia/Shanghai"),
        arxiv_fetch_max_results=_env_int("ARXIV_FETCH_MAX_RESULTS", 100),
        arxiv_page_size=_env_int("ARXIV_PAGE_SIZE", 25),
        arxiv_max_pages=_env_int("ARXIV_MAX_PAGES", 4),
        arxiv_page_delay_seconds=_env_int("ARXIV_PAGE_DELAY_SECONDS", 3),
        arxiv_request_timeout=_env_int("ARXIV_REQUEST_TIMEOUT", 60),
        arxiv_request_retries=_env_int("ARXIV_REQUEST_RETRIES", 5),
        arxiv_rate_limit_backoff_base=_env_int("ARXIV_RATE_LIMIT_BACKOFF_BASE", 60),
        arxiv_rate_limit_backoff_max=_env_int("ARXIV_RATE_LIMIT_BACKOFF_MAX", 600),
        arxiv_rate_limit_backoff_jitter=_env_int("ARXIV_RATE_LIMIT_BACKOFF_JITTER", 30),
        arxiv_rate_limit_graceful=_env_bool("ARXIV_RATE_LIMIT_GRACEFUL", True),
        max_papers=_env_int("MAX_PAPERS", 14),
        max_llm_papers=_env_int("MAX_LLM_PAPERS", 14),
        max_abstract_chars=_env_int("MAX_ABSTRACT_CHARS", 1800),
        llm_batch_size=_env_int("LLM_BATCH_SIZE", 5),
        min_rating=_env_int("MIN_RATING", 1),
        lookback_days=_env_int("LOOKBACK_DAYS", 1),
        deep_papers=_env_int("DEEP_PAPERS", 4),
        brief_papers=_env_int("BRIEF_PAPERS", 10),
        calendar_enabled=_env_bool("CALENDAR_ENABLED", True),
        calendar_events_path=_env("CALENDAR_EVENTS_PATH", "config/events.yaml"),
        calendar_conferences_path=_env("CALENDAR_CONFERENCES_PATH", "config/conferences.yaml"),
        calendar_horizon_days=_env_int("CALENDAR_HORIZON_DAYS", 120),
        calendar_recent_days=_env_int("CALENDAR_RECENT_DAYS", 14),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_base_url=os.getenv("OPENAI_BASE_URL"),
        openai_model=_default_model(),
        analysis_enabled=_env_bool("ANALYSIS_ENABLED", False),
        analysis_extract_model=_env_model("ANALYSIS_EXTRACT_MODEL", _default_model()),
        analysis_assess_model=_env_model("ANALYSIS_ASSESS_MODEL", _default_model()),
        analysis_extract_thinking=_env_tristate("ANALYSIS_EXTRACT_THINKING", _default_thinking())
        if os.getenv("ANALYSIS_EXTRACT_THINKING") is not None
        else False,
        analysis_assess_thinking=_env_tristate("ANALYSIS_ASSESS_THINKING"),
        llm_thinking=_env_tristate("LLM_THINKING", _default_thinking()),
        digest_json_path=_env("DIGEST_JSON_PATH", "digest.json"),
        profile_path=_env("PROFILE_PATH", "config/profile.yaml"),
        packs_dir=_env("PACKS_DIR", "config/packs"),
        fields=_split_csv(_env("FIELDS", "")),
        fulltext_enabled=_env_bool("FULLTEXT_ENABLED", True),
        fulltext_max_chars=_env_int("FULLTEXT_MAX_CHARS", 40000),
        fulltext_max_pages=_env_int("FULLTEXT_MAX_PAGES", 40),
        fulltext_timeout=_env_int("FULLTEXT_TIMEOUT", 120),
        journals_enabled=_env_bool("JOURNALS_ENABLED", True),
        journals_path=_env("JOURNALS_PATH", "config/journals.yaml"),
        journals_rows=_env_int("JOURNALS_ROWS", 40),
        journals_max_per=_env_int("JOURNALS_MAX_PER", 6),
        journals_timeout=_env_int("JOURNALS_TIMEOUT", 30),
        journals_lookback_days=_env_int("JOURNALS_LOOKBACK_DAYS", 2),
        journals_mailto=_env("JOURNALS_MAILTO", "arxiv-digest@example.com"),
        preprint_enabled=_env_bool("PREPRINT_ENABLED", True),
        preprint_timeout=_env_int("PREPRINT_TIMEOUT", 20),
        preprint_max_results=_env_int("PREPRINT_MAX_RESULTS", 5),
        preprint_abstract_lookups=_env_int("PREPRINT_ABSTRACT_LOOKUPS", 4),
        sources_enabled=_env_bool("SOURCES_ENABLED", True),
        sources_path=_env("SOURCES_PATH", "config/sources.yaml"),
        sources_max_items=_env_int("SOURCES_MAX_ITEMS", 6),
        sources_timeout=_env_int("SOURCES_TIMEOUT", 30),
        state_path=_env("STATE_PATH", "state.json"),
        trend_enabled=_env_bool("TREND_ENABLED", True),
        trend_rows=_env_int("TREND_ROWS", 4),
        pulse_enabled=_env_bool("PULSE_ENABLED", True),
        pulse_model=_env_model("PULSE_MODEL", _default_model()),
        pulse_threads=_env_int("PULSE_THREADS", 4),
        pulse_max_materials=_env_int("PULSE_MAX_MATERIALS", 30),
        pulse_thinking=_env_tristate("PULSE_THINKING", _default_thinking()),
        radar_enabled=_env_bool("RADAR_ENABLED", True),
        radar_path=_env("RADAR_PATH", "config/radar.yaml"),
        radar_per_field=_env_int("RADAR_PER_FIELD", 1),
        radar_max_fields=_env_int("RADAR_MAX_FIELDS", 5),
        radar_lookback_days=_env_int("RADAR_LOOKBACK_DAYS", 7),
        email_theme=_env("EMAIL_THEME", "dark").strip().lower() or "dark",
        email_attach_html=_env_bool("EMAIL_ATTACH_HTML", True),
        translate_enabled=_env_bool("TRANSLATE_ENABLED", True),
        translate_thinking=_env_tristate("TRANSLATE_THINKING", _default_thinking()),
        translate_limit=max(0, _env_int("TRANSLATE_LIMIT", 40)),
        radar_thinking=_env_tristate("RADAR_THINKING", _default_thinking()),
        feedback_url=_env("FEEDBACK_URL", ""),
        feedback_path=_env("FEEDBACK_PATH", "config/feedback.json"),
        feedback_sheet_csv_url=_env("FEEDBACK_SHEET_CSV_URL", ""),
        feedback_sheet_timeout=_env_int("FEEDBACK_SHEET_TIMEOUT", 20),
        zotero_api_key=os.getenv("ZOTERO_API_KEY"),
        zotero_user_id=os.getenv("ZOTERO_USER_ID"),
        zotero_library_type=_env("ZOTERO_LIBRARY_TYPE", "user"),
        zotero_collection_id=os.getenv("ZOTERO_COLLECTION_ID"),
        zotero_limit=_env_int("ZOTERO_LIMIT", 100),
        zotero_skip_collected=_env_bool("ZOTERO_SKIP_COLLECTED", False),
        smtp_host=_env("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=_env_int("SMTP_PORT", 587),
        smtp_username=os.getenv("SMTP_USERNAME", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        email_from=os.getenv("EMAIL_FROM", os.getenv("SMTP_USERNAME", "")),
        email_to=os.getenv("EMAIL_TO", ""),
        smtp_timeout=_env_int("SMTP_TIMEOUT", 30),
        cache_dir=_env("CACHE_DIR", ".cache/arxiv-digest"),
        run_report_path=_env("RUN_REPORT_PATH", "run_report.json"),
        llm_timeout=_env_int("LLM_TIMEOUT", 90),
        fulltext_parse_timeout=_env_int("FULLTEXT_PARSE_TIMEOUT", 30),
        zotero_share_with_model=_env_bool("ZOTERO_SHARE_WITH_MODEL", False),
        feedback_signing_key=os.getenv("FEEDBACK_SIGNING_KEY", ""),
        digest_edition=_env("DIGEST_EDITION", "auto").strip().lower(),
        weekly_enabled=_env_bool("WEEKLY_ENABLED", True),
        weekly_weekday=_env_int("WEEKLY_WEEKDAY", 6),
        weekly_max_papers=_env_int("WEEKLY_MAX_PAPERS", 12),
        weekly_deep_papers=_env_int("WEEKLY_DEEP_PAPERS", 6),
        weekly_fetch_max_results=_env_int("WEEKLY_FETCH_MAX_RESULTS", 600),
        weekly_min_signals=_env_int("WEEKLY_MIN_SIGNALS", 2),
        paper_relevance_enabled=_env_bool("PAPER_RELEVANCE_ENABLED", True),
        paper_min_signals=_env_int("PAPER_MIN_SIGNALS", 1),
        world_news_target=_env_int("WORLD_NEWS_TARGET", 30),
        world_news_max_items=_env_int("WORLD_NEWS_MAX_ITEMS", 36),
        world_astronomy_percent=_env_int("WORLD_ASTRONOMY_PERCENT", 50),
        world_news_max_age_days=_env_int("WORLD_NEWS_MAX_AGE_DAYS", 7),
    )
