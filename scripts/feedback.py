"""收反馈：把邮件里点的那一下记成 JSON。

v2 起一封邮件里的每篇论文有两个独立维度，邮件底部是六个按钮：

* 相关性 `rel`：2=直接相关 / 1=沾边 / 0=无关
* 有用性 `use`：2=很有用 / 1=一般 / 0=没用

点哪个只改哪一维，另一维保留上次的取值。旧的 `up/meh/down`
仍然可用（等价于两维同时取 2/1/0），方便历史 CSV 继续导入。

本地用法：

    # 记一维（另一维不动）
    python scripts/feedback.py record --id 2610.31660v1 --rel 2
    python scripts/feedback.py record --id 2610.31660v1 --use 1

    # 两维一起记
    python scripts/feedback.py record --id 2610.31660v1 --rel 0 --use 0

    # 兼容旧写法
    python scripts/feedback.py record --id 2610.31660v1 --vote up

    # 看学到了什么
    python scripts/feedback.py stats

    # 从 Google Sheet 导出的 CSV 批量导入（列：item,relevance,usefulness）
    python scripts/feedback.py import votes.csv

要接住邮件里的点击，跑 `python scripts/feedback_server.py`
（本机端点）或按 docs/FEEDBACK.md 部署一个 Google Apps Script。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arxiv_digest.config import load_config  # noqa: E402
from arxiv_digest.feedback import (  # noqa: E402
    format_plain_text,
    load_feedback,
    merge_csv,
    parse_feedback_csv,
    record_legacy_vote,
    record_vote,
    save_feedback,
    save_feedback_merged,
    combine_stores, store_from_dict, remember_papers,
)
from arxiv_digest.models import Paper  # noqa: E402


def _papers_from_digest(path: Path) -> dict[str, Paper]:
    import json
    from arxiv_digest.contracts import papers_from_payload
    if not path.is_file():
        return {}
    try:
        return {paper.id: paper for paper in papers_from_payload(json.loads(path.read_text(encoding="utf-8")))}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _level(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        level = int(value)
    except (TypeError, ValueError):
        return None
    return level if level in (0, 1, 2) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="记录/查看论文反馈（相关性 + 有用性）")
    parser.add_argument("--path", default="", help="反馈文件路径，默认取 FEEDBACK_PATH")
    parser.add_argument("--digest", default="", help="digest.json 路径，默认取 DIGEST_JSON_PATH")
    sub = parser.add_subparsers(dest="command")

    record_parser = sub.add_parser("record", help="记一票")
    record_parser.add_argument("--id", required=True)
    record_parser.add_argument("--rel", default="", help="相关性 2/1/0")
    record_parser.add_argument("--use", dest="use", default="", help="有用性 2/1/0")
    record_parser.add_argument("--vote", default="", choices=["", "up", "meh", "down"], help="旧接口")

    sub.add_parser("stats", help="看学到的偏好")

    import_parser = sub.add_parser("import", help="从 CSV 批量导入（列：item,relevance,usefulness）")
    import_parser.add_argument("file")

    args = parser.parse_args(argv)
    config = load_config()
    path = Path(args.path or config.feedback_path or "config/feedback.json")
    digest = Path(args.digest or config.digest_json_path or "digest.json")

    from arxiv_digest.state import load_state
    from arxiv_digest.identity import canonical_id
    store = combine_stores(store_from_dict(load_state(config.state_path).feedback), load_feedback(path))
    remember_papers(store, list(_papers_from_digest(digest).values()))
    if args.command == "record":
        papers = _papers_from_digest(digest)
        paper = papers.get(args.id)
        if paper is None and canonical_id(args.id) in store.history:
            meta = store.history[canonical_id(args.id)]
            paper = Paper(id=args.id, title=str(meta.get("title", "")), authors=list(meta.get("authors") or []),
                          categories=list(meta.get("categories") or []), summary="", published="", updated="", pdf_url="", abs_url="")
        if paper is None:
            # 没有 digest.json 也允许记：作者/分类学不到，但这一票本身不丢。
            paper = Paper(
                id=args.id,
                title="",
                authors=[],
                summary="",
                published="",
                updated="",
                categories=[],
                pdf_url="",
                abs_url="",
            )
            print(f"[WARN] {digest} 里没有 {args.id}，只记票、学不到作者/分类")
        if args.vote:
            if not record_legacy_vote(store, paper, args.vote):
                print("[FAIL] 未知的投票值")
                return 1
            shown = f"vote={args.vote}"
        else:
            relevance = _level(args.rel)
            usefulness = _level(args.use)
            if relevance is None and usefulness is None:
                print("[FAIL] 至少给一个 --rel 或 --use（取值 2/1/0）")
                return 1
            if not record_vote(store, paper, relevance=relevance, usefulness=usefulness):
                print("[FAIL] 没记下任何一维，检查取值是否在 2/1/0")
                return 1
            shown = f"rel={relevance} use={usefulness}"
        save_feedback_merged(path, store)
        print(f"[ OK ] 记下 {args.id} → {shown}（{path}）")
        print(format_plain_text(store))
        return 0

    if args.command == "import":
        rows = parse_feedback_csv(Path(args.file).read_text(encoding="utf-8"))
        added = merge_csv(store, rows)
        save_feedback_merged(path, store)
        print(f"[ OK ] 导入 {added} 行（CSV 共 {len(rows)} 行）")
        print(format_plain_text(store))
        return 0

    print(format_plain_text(store))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
