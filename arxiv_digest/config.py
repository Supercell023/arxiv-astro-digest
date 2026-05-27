from __future__ import annotations

import os
from dataclasses import dataclass


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env(name: str, default: str) -> str:
    return os.getenv(name) or default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {value!r}.") from exc


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
    arxiv_request_timeout: int
    arxiv_request_retries: int
    max_papers: int
    max_llm_papers: int
    max_abstract_chars: int
    min_rating: int
    lookback_days: int
    openai_api_key: str | None
    openai_base_url: str | None
    openai_model: str
    zotero_api_key: str | None
    zotero_user_id: str | None
    zotero_library_type: str
    zotero_collection_id: str | None
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    email_from: str
    email_to: str


def load_config() -> Config:
    _load_dotenv_if_available()

    return Config(
        arxiv_categories=_split_csv(_env("ARXIV_CATEGORIES", "astro-ph.GA,astro-ph.CO")),
        arxiv_keywords=_split_csv(_env("ARXIV_KEYWORDS", "")),
        digest_timezone=_env("DIGEST_TIMEZONE", "Asia/Shanghai"),
        arxiv_fetch_max_results=_env_int("ARXIV_FETCH_MAX_RESULTS", 100),
        arxiv_request_timeout=_env_int("ARXIV_REQUEST_TIMEOUT", 60),
        arxiv_request_retries=_env_int("ARXIV_REQUEST_RETRIES", 3),
        max_papers=_env_int("MAX_PAPERS", 12),
        max_llm_papers=_env_int("MAX_LLM_PAPERS", 12),
        max_abstract_chars=_env_int("MAX_ABSTRACT_CHARS", 1800),
        min_rating=_env_int("MIN_RATING", 1),
        lookback_days=_env_int("LOOKBACK_DAYS", 1),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_base_url=os.getenv("OPENAI_BASE_URL"),
        openai_model=_env("OPENAI_MODEL", "gpt-4o-mini"),
        zotero_api_key=os.getenv("ZOTERO_API_KEY"),
        zotero_user_id=os.getenv("ZOTERO_USER_ID"),
        zotero_library_type=_env("ZOTERO_LIBRARY_TYPE", "user"),
        zotero_collection_id=os.getenv("ZOTERO_COLLECTION_ID"),
        smtp_host=_env("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=_env_int("SMTP_PORT", 587),
        smtp_username=os.getenv("SMTP_USERNAME", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        email_from=os.getenv("EMAIL_FROM", os.getenv("SMTP_USERNAME", "")),
        email_to=os.getenv("EMAIL_TO", ""),
    )
