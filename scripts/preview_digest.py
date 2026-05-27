from __future__ import annotations

import logging

from arxiv_digest.config import load_config
from arxiv_digest.pipeline import run_pipeline


def main() -> None:
    config = load_config()
    result = run_pipeline(config)

    print(
        f"Fetched {len(result.papers)} papers; ranked {len(result.ranked_candidates)} candidates; "
        f"selected {len(result.ranked)} papers with min rating {config.min_rating}."
    )
    print()
    print(result.summary)
    print()
    for idx, paper in enumerate(result.enriched, start=1):
        print(f"{idx}. [{paper.rating}/5 stars, score {paper.score:.2f}] {paper.title}")
        print(f"   {paper.abs_url}")
        print(f"   中文摘要: {paper.chinese_summary}")
        if paper.score_reasons:
            print(f"   reasons: {'; '.join(paper.score_reasons)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()
