"""评测分析质量：不是"感觉写得好"，而是三条可数的事实。

1. **引文保真**：每条 `quote` 必须能在论文原文里原样找到（子串匹配）。编造的引文会被抓出来。
2. **数字覆盖**：预置的"该抽到的数"（样本量、红移范围、误差）有没有出现在关键数字里。
3. **字段覆盖**：七问里有多少条不是"摘要未提/全文未提"。

用法：

    # 1. 抓 20 篇最近的 astro-ph.CO 论文，生成评测集骨架
    python scripts/eval_analysis.py build --count 20

    # 2. 对冻结的全部输入逐篇分析（run 会调用已配置的模型；不抓 PDF）
    python scripts/eval_analysis.py run --out eval-digest.json
    python scripts/eval_analysis.py check --digest eval-digest.json

build 只调 arXiv；check 只读本地文件；run 需要模型凭据且可能计费。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arxiv_digest.arxiv_client import fetch_recent_papers  # noqa: E402
from arxiv_digest.contracts import paper_to_dict, papers_from_payload, DIGEST_VERSION
from arxiv_digest.identity import canonical_id
from arxiv_digest.storage import atomic_json

MISSING_LABELS = ("摘要未提", "全文未提", "全文节选未检出", "")
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
        "version": 2,
        "note": "由 scripts/eval_analysis.py build 自动生成；expected_numbers 来自摘要原文，可直接核验是否被漏抽",
        "categories": categories,
        "entries": entries,
        "items": [paper_to_dict(paper) for paper in papers[:count]],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ OK ] 写入 {len(entries)} 篇 → {path}")
    print("下一步：run --out eval-digest.json 对本集合的全部冻结输入分析，再执行 check")
    return 0


def run_set(eval_path: Path, output: Path, *, client=None, config=None) -> int:
    """Analyze exactly the frozen set; never silently re-fetch or use daily quotas."""
    from arxiv_digest.config import load_config
    from arxiv_digest.pipeline import _assessment_packs, _build_client
    from arxiv_digest.profile import load_profile
    from arxiv_digest.analysis import analyze_papers, PROMPT_VERSION
    from arxiv_digest.llm_client import LLMOptions
    config = config or load_config()
    payload = json.loads(eval_path.read_text(encoding="utf-8"))
    expected = [canonical_id(str(row["id"])) for row in payload.get("entries", [])]
    index = {canonical_id(paper.id): paper for paper in papers_from_payload(payload)}
    if not expected or len(set(expected)) != len(expected) or any(key not in index for key in expected):
        print("[FAIL] 评测集缺少完整冻结输入或 ID 重复；请重新 build，禁止用另一次抓取代替。")
        return 1
    client = client if client is not None else _build_client(config)
    if client is None:
        print("[FAIL] 没有可用的模型客户端；run 不会把未分析输入标记为通过。")
        return 1
    papers = [index[key] for key in expected]
    # Clear old analysis to make failed new requests distinguishable from old successes.
    from dataclasses import replace
    papers = [replace(paper, analysis=None, chinese_summary="") for paper in papers]
    packs = _assessment_packs(config)
    extract_options = LLMOptions(temperature=1.0, max_tokens=6000, thinking=config.analysis_extract_thinking)
    assess_options = LLMOptions(temperature=1.0, max_tokens=9000, thinking=config.analysis_assess_thinking)
    analyzed = analyze_papers(client, papers, load_profile(config.profile_path), packs,
        limit=len(papers), extract_model=config.analysis_extract_model, assess_model=config.analysis_assess_model,
        cache_dir=config.cache_dir, extract_options=extract_options, assess_options=assess_options)
    failed = [paper.id for paper in analyzed if paper.analysis is None]
    import hashlib
    from dataclasses import asdict
    def fingerprint(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path and Path(path).is_file() else None
    atomic_json(output, {"version": DIGEST_VERSION, "prompt_version": PROMPT_VERSION,
        "items": [paper_to_dict(paper) for paper in analyzed], "run": {"mode": "fixed_evaluation",
        "basis": "frozen abstracts", "expected": len(expected), "failed_ids": failed,
        "extract_model": config.analysis_extract_model, "assess_model": config.analysis_assess_model,
        "extract_options": asdict(extract_options), "assess_options": asdict(assess_options),
        "profile_sha256": fingerprint(config.profile_path),
        "pack_sha256": {pack.id: fingerprint(pack.source_path) for pack in packs}}})
    print(f"分析完成：{len(analyzed) - len(failed)}/{len(expected)}；输出 {output}")
    return 1 if failed else 0


def _text_of(item: dict) -> str:
    parts = [item.get("summary") or ""]
    parts.append((item.get("analysis") or {}).get("source_text") or "")
    fulltext = item.get("fulltext") or ""
    if fulltext:
        parts.append(fulltext)
    return "\n".join(parts)


def check(eval_path: Path, digest_path: Path) -> int:
    eval_set = json.loads(eval_path.read_text(encoding="utf-8"))
    digest = json.loads(digest_path.read_text(encoding="utf-8"))

    items = {canonical_id(str(item.get("id"))): item for item in digest.get("items") or []}
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
        item = items.get(canonical_id(str(entry.get("id"))))
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

    ok = (covered == len(entries) and quote_total > 0 and quote_ok / quote_total >= 0.9
          and (not number_total or number_hit / number_total >= 0.8))
    print()
    print("[ OK ] 覆盖完整、引文保真≥90%、数字召回≥80%" if ok else "[FAIL] 覆盖、引文或数字召回未达标，检查报告")
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
    run_parser = sub.add_parser("run", help="调用模型逐篇分析固定评测集，可能计费；不重新抓取")
    run_parser.add_argument("--out", default="eval-digest.json")

    args = parser.parse_args(argv)
    path = Path(args.set)
    if args.command == "build":
        categories = [part.strip() for part in args.categories.split(",") if part.strip()]
        return build(path, count=args.count, categories=categories, days=args.days)
    if args.command == "check":
        candidates = [Path(args.digest), path.parent.parent / "digest.json"]
        digest = next((item for item in candidates if item.is_file()), None)
        if digest is None:
            print("[FAIL] 找不到 digest.json，设置 DIGEST_JSON_PATH=digest.json 后运行 --out 预览")
            return 1
        return check(path, digest)
    if args.command == "run":
        return run_set(path, Path(args.out))
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
