"""抓一次真实材料，算出台词化的"主题命中表"，写进预览数据。

用途：让 `preview.html` 里的「本周脉搏」来自**真实抓取**，而不是编的。
它只做统计（哪些主题被提了多少次、哪些源都在说、哪些条目带张力措辞），
不做判断——判断那一步要么由模型在有 key 时完成，要么由人写进 replay。

    python scripts/refresh_pulse.py            # 写进 config/preview_replay.json
    python scripts/refresh_pulse.py --print    # 只打印，不落盘
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arxiv_digest.arxiv_client import fetch_recent_papers  # noqa: E402
from arxiv_digest.packs import load_packs, select_packs  # noqa: E402
from arxiv_digest.pulse import count_topics, materials_from  # noqa: E402
from arxiv_digest.sources import KIND_SECONDARY, KIND_TALK, collect_sources, merge_items  # noqa: E402


def gather(*, days: int, max_papers: int) -> tuple[list, list]:
    papers = fetch_recent_papers(
        ["astro-ph.CO", "astro-ph.GA"],
        days,
        max_results=max_papers,
        page_size=25,
        max_pages=3,
        page_delay_seconds=3,
        timeout=60,
        retries=3,
    )
    results = collect_sources("config/sources.yaml", max_items=6, timeout=30)
    secondary = merge_items(results, kind=KIND_SECONDARY)
    talks = merge_items(results, kind=KIND_TALK)
    return papers, materials_from(papers, secondary, talks, max_papers=max_papers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="刷新预览用的脉搏材料")
    parser.add_argument("--replay", type=Path, default=ROOT / "config" / "preview_replay.json")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--max", type=int, default=40)
    parser.add_argument("--print", dest="print_only", action="store_true")
    args = parser.parse_args(argv)

    papers, materials = gather(days=args.days, max_papers=args.max)
    packs = select_packs(load_packs("config/packs"), ())
    table = [topic for pack in packs for topic in pack.topics]
    topics = count_topics(materials, table)

    payload = {
        "materials": [
            {
                "id": material.id,
                "kind": material.kind,
                "title": material.title,
                "source": material.source,
                "date": material.date,
                "url": material.url,
                "text": material.text[:400],
            }
            for material in materials
        ],
        "topics": [
            {
                "label": topic.label,
                "hits": topic.hits,
                "sources": list(topic.sources),
                "item_ids": list(topic.item_ids),
                "tension_items": list(topic.tension_items),
            }
            for topic in topics
        ],
    }

    print(f"论文 {len(papers)} 篇，材料 {len(materials)} 条，主题 {len(topics)} 个")
    for topic in topics:
        flag = "跨源" if topic.is_shared else "单源"
        print(f"  {topic.label}: {topic.hits} 次 / {len(topic.item_ids)} 条 / {flag} / 张力 {len(topic.tension_items)}")

    if args.print_only:
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
        return 0

    replay = json.loads(args.replay.read_text(encoding="utf-8"))
    replay["pulse_materials"] = payload["materials"]
    replay["pulse_topics"] = payload["topics"]
    args.replay.write_text(json.dumps(replay, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {args.replay}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
