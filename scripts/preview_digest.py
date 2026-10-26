from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from dataclasses import replace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from arxiv_digest.calendar import format_plain_text
from arxiv_digest.config import load_config
from arxiv_digest.emailer import render_email_html
from arxiv_digest.pipeline import run_pipeline, update_diagnostics


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="跑一次真实流程并打印摘要；加 --out 可落成 HTML（不发信）。")
    parser.add_argument("--theme", default="dark", choices=("dark", "light"), help="邮件主题")
    parser.add_argument("--out", type=Path, default=None, help="把渲染结果写到这个 HTML 文件，例如 preview_live.html")
    parser.add_argument("--edition", choices=("auto", "daily", "weekly"))
    args = parser.parse_args(argv)

    config = load_config()
    if args.edition:
        config = replace(config, digest_edition=args.edition)
    # 预览不写 state.json：否则"看一眼"会把条目算成已推送。
    result = run_pipeline(config, persist_state=False, preview=True)
    result.report["delivery"] = "preview"
    update_diagnostics(config, result)

    print(
        f"Fetched {len(result.papers)} papers; ranked {len(result.ranked_candidates)} candidates; "
        f"selected {len(result.ranked)} papers with min rating {config.min_rating}."
    )
    print()
    print(result.summary)
    print()
    print("== 近期日程 ==")
    print(format_plain_text(result.calendar))
    print()
    if result.sources:
        print("== 信息源 ==")
        for src in result.sources:
            print(f"  {src.name}: {src.status_label}")
        print()
    if result.secondary:
        print(f"== 世界在变 · {len(result.secondary)} 条 ==")
        for item in result.secondary:
            print(f"  · {item.title}  [{item.source}] {item.url}")
        print()
    if result.talks:
        print(f"== 会议报告 · {len(result.talks)} 条 ==")
        for item in result.talks:
            print(f"  · {item.title}  [{item.source}] {item.url}")
        print()
    if result.trend:
        print("== 本周趋势 ==")
        for row in result.trend:
            print(f"  {row.label}: 本周 {row.this_week} / 上周 {row.last_week} {row.arrow} {row.label_text}")
        print()
    if result.radar and not result.radar.is_empty:
        print(f"== 邻域雷达 · {len(result.radar.blocks)} 个方向 ==")
        for block in result.radar.blocks:
            if block.headline:
                print(f"  · {block.neighborhood}: {block.headline.title}")
        print()
    for idx, paper in enumerate(result.enriched, start=1):
        print(f"{idx}. [{paper.rating}/5 stars, score {paper.score:.2f}] {paper.title}")
        print(f"   {paper.abs_url}")
        print(f"   中文摘要: {paper.chinese_summary}")
        if paper.score_reasons:
            print(f"   reasons: {'; '.join(paper.score_reasons)}")

    if args.out:
        html = render_email_html(
            result.summary,
            result.enriched,
            result.calendar,
            timezone_name=config.digest_timezone,
            deep_papers=result.report.get("deep_papers", config.deep_papers),
            brief_papers=result.report.get("brief_papers", config.brief_papers),
            edition=result.report.get("edition", "daily"),
            period_label=result.report.get("period_label", ""),
            news_counts=result.report.get("news_counts", {}),
            secondary=result.secondary,
            talks=result.talks,
            trend=result.trend,
            sources=result.sources,
            pulse=result.pulse,
            radar=result.radar,
            feedback_url=config.feedback_url,
            feedback_signing_key=config.feedback_signing_key,
            window_days=result.window_days,
            theme=args.theme,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(html, encoding="utf-8")
        print()
        print(f"已写出 {args.out}（{len(html):,} 字符）——直接用浏览器打开即可，没有发信。")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()
