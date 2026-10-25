"""评测分析质量：不是"感觉写得好"，而是三条可数的事实。

1. **引文保真**：每条 `quote` 必须能在论文原文里原样找到（子串匹配）。编造的引文会被抓出来。
2. **数字覆盖**：预置的"该抽到的数"（样本量、红移范围、误差）有没有出现在关键数字里。
3. **字段覆盖**：七问里有多少条不是"摘要未提/全文未提"。

用法：

    # 1. 抓 20 篇最近的 astro-ph.CO 论文，生成评测集骨架
    python scripts/eval_analysis.py build --count 20

    # 2. 跑完一次真实摘要后，用 digest.json 评分
    python scripts/eval_analysis.py check --digest digest.json

两个命令都不需要 API key：build 只调 arXiv，check 只读本地文件。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arxiv_digest.arxiv_client import fetch_recent_papers  # noqa: E402

MISSING_LABELS = ("摘要未提", "全文未提", "")
_UNIT_WORDS = (
    "%",
    "km/s",
    "km s",
    "Mpc",
    "Gpc",
    "kpc",
    "dex",
    "arcmin",
    "arcsec",
    "deg",
    "galaxies",
    "spectra",
    "objects",
    "quasars",
    "sources",
    "clusters",
    "halos",
    "M⊙",
    "sigma",
    "S/N",
    "redshift",
    "λ",
)
_TOKEN_RE = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*(?:\s*±\s*\d+(?:\.\d+)?)?")
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def _salient_numbers(text: str, *, limit: int = 6) -> list[str]:
    """从摘要里挑"会被引用的数"：附近有单位词，或数量级足够大。"""

    found: dict[str, None] = {}
    for match in _TOKEN_RE.finditer(text):
        token = match.group(0).strip()
        if not token or _YEAR_RE.match(token):
            continue
        window = text[max(0, match.start() - 40) : match.end() + 40]
        has_unit = any(word in window for word in _UNIT_WORDS)
        head = token.replace(",", "").split(".")[0]
        big = head.isdigit() and int(head) >= 1000
        if has_unit or big:
            found[token] = None
        if len(found) >= limit:
            break
    return list(found)

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def build(path: Path, *, count: int, categories: list[str], days: int) -> int:
    papers = fetch_recent_papers(categories, lookback_days=days, max_results=count)
    if not papers:
        print("[FAIL] 没抓到论文，检查网络或分类")
        return 1

    entries = []
    for paper in papers[:count]:
        numbers = _salient_numbers(paper.summary)
        entries.append(
            {
                "id": paper.id,
                "title": paper.title,
                "categories": list(paper.categories),
                "expected_numbers": numbers[:6],
                "must_cite": False,
            }
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "note": "由 scripts/eval_analysis.py build 自动生成；expected_numbers 来自摘要原文，可直接核验是否被漏抽",
        "categories": categories,
        "entries": entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ OK ] 写入 {len(entries)} 篇 → {path}")
    print("下一步：跑一次真实摘要（ANALYSIS_ENABLED=true, DIGEST_JSON_PATH=digest.json），再执行 check")
    return 0


def _text_of(item: dict) -> str:
    parts = [item.get("summary") or ""]
    fulltext = item.get("fulltext") or ""
    if fulltext:
        parts.append(fulltext)
    return "\n".join(parts)


def check(eval_path: Path, digest_path: Path) -> int:
    eval_set = json.loads(eval_path.read_text(encoding="utf-8"))
    digest = json.loads(digest_path.read_text(encoding="utf-8"))

    items = {str(item.get("id")): item for item in digest.get("items") or []}
    entries = eval_set.get("entries") or []
    if not entries:
        print("[FAIL] 评测集是空的")
        return 1

    quote_total = quote_ok = 0
    field_total = field_filled = 0
    number_total = number_hit = 0
    covered = 0
    problems: list[str] = []

    for entry in entries:
        item = items.get(str(entry.get("id")))
        analysis = (item or {}).get("analysis") or {}
        if not analysis:
            problems.append(f"{entry.get('id')}: 没有被分析（或没进精读）")
            continue
        covered += 1
        haystack = _text_of(item)
        haystack_lower = haystack.lower()

        for key, fact in (analysis.get("facts") or {}).items():
            if not isinstance(fact, dict):
                continue
            field_total += 1
            value = (fact.get("value") or "").strip()
            if value and value not in MISSING_LABELS:
                field_filled += 1
            quote = (fact.get("quote") or "").strip()
            if not quote:
                continue
            quote_total += 1
            if quote.lower().strip("\"'“” ") in haystack_lower:
                quote_ok += 1
            elif value not in MISSING_LABELS:
                problems.append(f"{entry.get('id')}/{key}: 引文在原文里找不到 → {quote[:60]}")

        numbers = [str(num.get("value") or "") for num in analysis.get("numbers") or []]
        numbers_blob = " ".join(numbers)
        for expected in entry.get("expected_numbers") or []:
            number_total += 1
            if expected and expected in numbers_blob:
                number_hit += 1

    def _pct(part: int, whole: int) -> str:
        return f"{part}/{whole} ({part / whole * 100:.0f}%)" if whole else "n/a"

    print("评测报告")
    print(f"  覆盖论文        {_pct(covered, len(entries))}")
    print(f"  引文保真        {_pct(quote_ok, quote_total)}")
    print(f"  字段填充        {_pct(field_filled, field_total)}")
    print(f"  关键数字召回    {_pct(number_hit, number_total)}")
    if problems:
        print(f"  发现问题 {len(problems)} 条（前 8 条）：")
        for line in problems[:8]:
            print(f"    - {line}")

    ok = quote_total > 0 and quote_ok / quote_total >= 0.9
    print()
    print("[ OK ] 引文保真达标（≥90%）" if ok else "[FAIL] 引文保真不足 90%，先别信评论内容")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="分析质量评测集")
    parser.add_argument("--set", default="config/eval_set.json", help="评测集路径")
    sub = parser.add_subparsers(dest="command")

    build_parser = sub.add_parser("build", help="抓论文生成评测集骨架")
    build_parser.add_argument("--count", type=int, default=20)
    build_parser.add_argument("--categories", default="astro-ph.CO,astro-ph.GA")
    build_parser.add_argument("--days", type=int, default=5)

    check_parser = sub.add_parser("check", help="按 digest.json 评分")
    check_parser.add_argument("--digest", default="digest.json")

    args = parser.parse_args(argv)
    path = Path(args.set)
    if args.command == "build":
        categories = [part.strip() for part in args.categories.split(",") if part.strip()]
        return build(path, count=args.count, categories=categories, days=args.days)
    if args.command == "check":
        candidates = [Path(args.digest), path.parent.parent / "digest.json"]
        digest = next((item for item in candidates if item.is_file()), None)
        if digest is None:
            print("[FAIL] 找不到 digest.json，先跑一次 `python -m arxiv_digest.main --dry-run`")
            return 1
        return check(path, digest)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
