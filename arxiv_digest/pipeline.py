from __future__ import annotations

from dataclasses import dataclass

from .arxiv_client import fetch_recent_papers
from .config import Config
from .llm import generate_digest_content
from .models import Paper
from .recommender import filter_by_min_rating, rank_papers
from .zotero_client import fetch_zotero_preferences


@dataclass
class PipelineResult:
    papers: list[Paper]
    ranked_candidates: list[Paper]
    ranked: list[Paper]
    summary: str
    enriched: list[Paper]


def run_pipeline(config: Config) -> PipelineResult:
    papers = fetch_recent_papers(
        config.arxiv_categories,
        config.lookback_days,
        max_results=config.arxiv_fetch_max_results,
        timeout=config.arxiv_request_timeout,
        retries=config.arxiv_request_retries,
        timezone_name=config.digest_timezone,
    )
    preferences = fetch_zotero_preferences(
        config.zotero_api_key,
        config.zotero_user_id,
        config.zotero_library_type,
        config.zotero_collection_id,
    )
    ranked_candidates = rank_papers(papers, config.arxiv_keywords, preferences, config.arxiv_fetch_max_results)
    ranked = filter_by_min_rating(ranked_candidates, config.min_rating)[: config.max_papers]
    summary, enriched = generate_digest_content(
        config.openai_api_key,
        config.openai_model,
        ranked,
        max_llm_papers=config.max_llm_papers,
        max_abstract_chars=config.max_abstract_chars,
        base_url=config.openai_base_url,
    )
    return PipelineResult(
        papers=papers,
        ranked_candidates=ranked_candidates,
        ranked=ranked,
        summary=summary,
        enriched=enriched,
    )
