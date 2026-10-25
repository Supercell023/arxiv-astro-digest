"""收反馈：把邮件里点的那一下记成 JSON。

用法（在点开表单/Worker 之前，先本地跑通闭环）：

    # 记一票：需要从 digest.json 里找到这篇的元数据
    python scripts/feedback.py record --id 2610.31660v1 --vote up

    # 看学到了什么
    python scripts/feedback.py stats

    # 如果 URL 参数被转进了 CSV（item,v,t），批量导入
    python scripts/feedback.py import votes.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arxiv_digest.config import load_config  # noqa: E402
from arxiv_digest.feedback import (  # noqa: E402
    FeedbackStore,
    format_plain_text,
    load_feedback,
    record_vote,
    save_feedback,
)
from arxiv_digest.models import Paper  # noqa: E402


def _papers_from_digest(path: Path) -> dict[str, Paper]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, Paper] = {}
    for raw in payload.get("items") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        result[str(raw["id"])] = Paper(
            id=str(raw["id"]),
            title=str(raw.get("title") or ""),
            authors=list(raw.get("authors") or []),
            summary=str(raw.get("summary") or ""),
            published=str(raw.get("published") or ""),
            updated=str(raw.get("updated") or ""),
            categories=list(raw.get("categories") or []),
            pdf_url=str(raw.get("pdf_url") or ""),
            abs_url=str(raw.get("abs_url") or ""),
            score=float(raw.get("score") or 0.0),
            rating=int(raw.get("rating") or 0),
            score_reasons=list(raw.get("score_reasons") or []),
            chinese_summary=str(raw.get("chinese_summary") or ""),
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="记录/查看论文反馈")
    parser.add_argument("--path", default="", help="反馈文件路径，默认取 FEEDBACK_PATH")
    parser.add_argument("--digest", default="", help="digest.json 路径，默认取 DIGEST_JSON_PATH")
    sub = parser.add_subparsers(dest="command")

    record_parser = sub.add_parser("record", help="记一票")
    record_parser.add_argument("--id", required=True)
    record_parser.add_argument("--vote", required=True, choices=["up", "meh", "down"])

    stats_parser = sub.add_parser("stats", help="看学到的偏好")
    stats_parser.set_defaults(command="stats")

    import_parser = sub.add_parser("import", help="从 CSV 批量导入（列：item,v）")
    import_parser.add_argument("file")

    args = parser.parse_args(argv)
    config = load_config()
    path = Path(args.path or "config/feedback.json")
    digest = Path(args.digest or config.digest_json_path or "digest.json")

    store = load_feedback(path)
    if args.command == "record":
        papers = _papers_from_digest(digest)
        paper = papers.get(args.id)
        if paper is None:
            print(f"[FAIL] {digest} 里没有 {args.id}（先跑一次生成 digest.json）")
            return 1
        if not record_vote(store, paper, args.vote):
            print("[FAIL] 未知的投票值")
            return 1
        save_feedback(path, store)
        print(f"[ OK ] 记下 {args.id} → {args.vote}（{path}）")
        print(format_plain_text(store))
        return 0

    if args.command == "import":
        papers = _papers_from_digest(digest)
        rows = 0
        with open(args.file, encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                item_id = (row.get("item") or row.get("id") or "").strip()
                vote = (row.get("v") or row.get("vote") or "").strip().lower()
                paper = papers.get(item_id)
                if paper and record_vote(store, paper, vote):
                    rows += 1
        save_feedback(path, store)
        print(f"[ OK ] 导入 {rows} 票")
        print(format_plain_text(store))
        return 0

    print(format_plain_text(store))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
