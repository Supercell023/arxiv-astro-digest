from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any

from .models import Paper
from .evidence import numbers_supported
from .llm_client import LLMOptions, _parse_json_object, call_json  # noqa: F401 - 兼容旧导入

LOGGER = logging.getLogger(__name__)

# 单批论文数：14 篇塞进一个 prompt 时，模型输出很容易在中途被截断，
# 一旦截断就是 json.loads 失败、整封邮件退化英文。改成小批后，
# 单批失败只影响这一批，其余批次的中文摘要照常保留。
DEFAULT_BATCH_SIZE = 5

# 有些兼容 OpenAI 协议的网关即使给了 response_format 也会返回 ```json 围栏，
# 或者前后带一句说明文字；这里统一做"去围栏 + 截取首尾大括号"的兜底解析。
_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _fallback_summary(papers: list[Paper]) -> str:
    if not papers:
        return "本期采集范围内没有找到符合兴趣与配置的论文。"
    top_titles = "；".join(paper.title for paper in papers[:3])
    return f"共筛选出 {len(papers)} 篇论文。优先关注：{top_titles}。"


def _fallback_paper_summary(paper: Paper) -> str:
    """没有中文摘要时不要伪造：把英文原文塞进"中文摘要"位置会被读成没翻完。

    模板对有内容的中文摘要正常渲染；为空时显示一行说明，英文原摘要仍在
    下方的"展开中英文摘要"里。
    """

    return ""


def _copy_paper(paper: Paper, **updates: object) -> Paper:
    return Paper(**{**paper.__dict__, **updates})


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars // 2:
        return truncated[:last_space].rstrip() + "..."
    return truncated.rstrip() + "..."


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CLAUSE_RE = re.compile(r"[^。！？；;]+[。！？；;]?")

# 缺摘要时的单篇重试上限：批量里漏掉几篇很常见，逐篇补比重跑整批便宜。
MAX_SINGLE_RETRIES = 6


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _summary_key(key: Any, sources: dict[str, str]) -> str:
    """把模型回显的 ID 对齐到论文 ID：容忍大小写、缺 vN、带 arXiv: 前缀。"""

    raw = str(key).strip()
    if raw in sources:
        return raw
    lowered = raw.lower().removeprefix("arxiv:").strip()
    for paper_id in sources:
        candidate = paper_id.lower()
        if candidate == lowered or candidate.removesuffix("v1") == lowered:
            return paper_id
        if candidate.startswith(lowered) and candidate[len(lowered):].startswith("v"):
            return paper_id
    return ""


def _salvage_summary(value: str, source: str) -> str:
    """数字回查失败时只丢掉含编造数字的分句，而不是整段作废。"""

    if numbers_supported(value, source):
        return value
    kept = "".join(
        clause for clause in _CLAUSE_RE.findall(value) if clause.strip() and numbers_supported(clause, source)
    ).strip()
    if len(kept) < 12 or not _has_cjk(kept):
        return ""
    # 删掉的分句太多时，剩下的是半句话（"工作并将……"），不如让单篇重试重写。
    if len(value.strip()) >= 80 and len(kept) < 0.6 * len(value.strip()):
        return ""
    return kept


def add_fallback_paper_summaries(papers: list[Paper]) -> list[Paper]:
    return [
        _copy_paper(paper, chinese_summary=paper.chinese_summary or _fallback_paper_summary(paper))
        for paper in papers
    ]


def _chunk(items: list[Paper], size: int) -> list[list[Paper]]:
    step = max(1, size)
    return [items[index : index + step] for index in range(0, len(items), step)]


def _build_paper_block(batch: list[Paper], max_abstract_chars: int) -> str:
    return "\n\n".join(
        "\n".join(
            [
                f"ID: {paper.id}",
                f"Title: {paper.title}",
                f"Authors: {', '.join(paper.authors[:5])}",
                f"Categories: {', '.join(paper.categories)}",
                f"Interest rating: {paper.rating}/5",
                f"Abstract: {_truncate_text(paper.summary, max_abstract_chars)}",
            ]
        )
        for paper in batch
    )


def _build_prompt(paper_block: str, *, ask_overall: bool, avoid_numbers: bool = False) -> str:
    if ask_overall:
        schema = """{
  "overall_summary": "4-6 句中文总览，指出共同主题、值得注意的方法或结果，并保持谨慎",
  "paper_summaries": {
    "论文 ID": "对应论文的 2-3 句中文单篇摘要，说明核心问题、方法/数据、主要结论或价值"
  }
}"""
        extra = ""
    else:
        schema = """{
  "paper_summaries": {
    "论文 ID": "对应论文的 2-3 句中文单篇摘要，说明核心问题、方法/数据、主要结论或价值"
  }
}"""
        extra = "\n本次只需返回 paper_summaries，不需要 overall_summary。"
    number_rule = (
        "\n4. 本次改写不要出现任何数字（样本量、比例、年份都用文字描述），把数字留给读者回原文核对。"
        if avoid_numbers
        else ""
    )
    return f"""请用中文为一位天文学/天体物理研究者整理以下 arXiv 新论文。

请只返回 JSON，不要使用 Markdown。JSON 格式必须为：
{schema}

要求：
1. 每篇论文都必须在 paper_summaries 中有一条中文摘要。
2. 不要夸大结论；如果只来自摘要，请保持谨慎措辞。
3. 单篇摘要面向研究者，避免泛泛而谈。{extra}{number_rule}

论文列表：
{paper_block}
"""


def _call_batch(
    client: Any,
    model: str,
    batch: list[Paper],
    *,
    ask_overall: bool,
    max_abstract_chars: int,
    thinking: bool | None = None,
    avoid_numbers: bool = False,
) -> tuple[str | None, dict[str, str]]:
    prompt = _build_prompt(
        _build_paper_block(batch, max_abstract_chars), ask_overall=ask_overall, avoid_numbers=avoid_numbers
    )
    payload = call_json(
        client,
        model,
        prompt,
        system="你是谨慎、专业的天文学论文助理，只输出可解析 JSON。",
        options=LLMOptions(temperature=0.3, max_tokens=4000, thinking=thinking),
    )

    raw_summaries = payload.get("paper_summaries")
    summaries: dict[str, str] = {}
    if isinstance(raw_summaries, dict):
        sources = {paper.id: _truncate_text(paper.summary, max_abstract_chars) for paper in batch}
        for key, value in raw_summaries.items():
            if not isinstance(value, str) or not value.strip():
                continue
            paper_id = _summary_key(key, sources)
            if not paper_id:
                LOGGER.debug("模型回了一个对不上的论文 ID：%r", key)
                continue
            text = value.strip()
            if not _has_cjk(text):
                LOGGER.debug("模型对 %s 返回的不是中文摘要，已丢弃。", paper_id)
                continue
            kept = _salvage_summary(text, sources[paper_id])
            if kept:
                summaries[paper_id] = kept

    overall = payload.get("overall_summary")
    overall_text = overall.strip() if isinstance(overall, str) and overall.strip() else None
    return overall_text, summaries


def generate_digest_summary(api_key: str | None, model: str, base_url: str | None, papers: list[Paper]) -> str:
    summary, _ = generate_digest_content(api_key, model, papers, base_url=base_url)
    return summary


def generate_digest_content(
    api_key: str | None,
    model: str,
    papers: list[Paper],
    max_llm_papers: int = 12,
    max_abstract_chars: int = 1800,
    base_url: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    thinking: bool | None = None,
    timeout: float = 90,
    edition_label: str = "本期",
    period_label: str = "",
) -> tuple[str, list[Paper]]:
    if not papers:
        return _fallback_summary(papers), []

    llm_papers = papers[: max(0, max_llm_papers)]
    if not api_key or not llm_papers:
        return _fallback_summary(papers), add_fallback_paper_summaries(papers)

    try:
        from openai import OpenAI
    except ImportError:
        LOGGER.warning("openai 未安装，使用降级摘要。")
        return _fallback_summary(papers), add_fallback_paper_summaries(papers)

    try:
        if base_url:
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=2)
        else:
            client = OpenAI(api_key=api_key, timeout=timeout, max_retries=2)
    except Exception as exc:  # pragma: no cover - 客户端构造失败极少见
        LOGGER.warning("初始化 OpenAI 客户端失败：%s", exc)
        return _fallback_summary(papers), add_fallback_paper_summaries(papers)

    batches = _chunk(llm_papers, batch_size)
    summaries: dict[str, str] = {}
    overall: str | None = None
    failed_batches = 0
    total_batches = len(batches)

    for index, batch in enumerate(batches, start=1):
        try:
            batch_overall, batch_summaries = _call_batch(
                client,
                model,
                batch,
                ask_overall=False,
                max_abstract_chars=max_abstract_chars,
                thinking=thinking,
            )
        except Exception as exc:
            failed_batches += 1
            LOGGER.warning("第 %d/%d 批（%d 篇）生成失败：%s", index, total_batches, len(batch), exc)
            continue
        summaries.update(batch_summaries)

    # 批量里漏条目很常见：把缺的几篇单独再要一次，比整批重跑便宜得多。
    missing = [paper for paper in llm_papers if not summaries.get(paper.id)]
    if missing and failed_batches < total_batches:
        attempts = missing[:MAX_SINGLE_RETRIES]
        recovered = 0
        for paper in attempts:
            for avoid_numbers in (False, True):
                try:
                    _, single = _call_batch(
                        client,
                        model,
                        [paper],
                        ask_overall=False,
                        max_abstract_chars=max_abstract_chars,
                        thinking=thinking,
                        avoid_numbers=avoid_numbers,
                    )
                except Exception as exc:  # noqa: BLE001 - 单篇失败不应影响其余论文
                    LOGGER.warning("单篇补摘要失败（%s）：%s", paper.id, type(exc).__name__)
                    break
                text = single.get(paper.id)
                if text:
                    summaries[paper.id] = text
                    recovered += 1
                    break
        LOGGER.warning(
            "中文摘要补齐：%d 篇批内缺摘要，单篇重试 %d 篇，补回 %d 篇。",
            len(missing),
            len(attempts),
            recovered,
        )

    enriched = [
        _copy_paper(
            paper,
            chinese_summary=summaries.get(paper.id) or paper.chinese_summary or _fallback_paper_summary(paper),
        )
        for paper in papers
    ]

    if failed_batches < total_batches:
        overview_rows = [
            {"id": paper.id, "title": paper.title,
             "abstract": _truncate_text(paper.summary, max_abstract_chars),
             "summary_zh": summaries.get(paper.id, "")}
            for paper in papers
        ]
        counts = Counter(paper.categories[0] for paper in papers if paper.categories)
        count_line = f"本次共 {len(papers)} 篇入选"
        if counts:
            count_line += "：" + "、".join(f"{name} {count} 篇" for name, count in counts.most_common())
        count_line += "。"
        try:
            payload = call_json(client, model,
                f'根据全部入选论文写4-6句中文{edition_label}总览，研究窗口：{period_label or "本次采集窗口"}。'
                '只用给出的事实，说明共同主题与差异，不要把一周的论文说成今天全部新发表。'
                f'{count_line}这是唯一的采集规模信息，其他数字必须来自下面的材料。'
                '只输出 JSON：{"overall_summary":"..."}。\n' + json.dumps(overview_rows, ensure_ascii=False),
                system="你是谨慎的论文编辑，不要把部分材料说成领域全貌。",
                options=LLMOptions(temperature=0.3, max_tokens=2000, thinking=thinking))
            raw_overall = payload.get("overall_summary")
            source = count_line + " " + " ".join(row["abstract"] for row in overview_rows)
            overall = (raw_overall.strip() if isinstance(raw_overall, str) and raw_overall.strip()
                       and numbers_supported(raw_overall, source) else None)
        except Exception as exc:
            LOGGER.warning("全量总览生成失败：%s", type(exc).__name__)

    if overall is None:
        summary = _fallback_summary(papers)
        if failed_batches < total_batches:
            summary += "\n\n（本次未能生成总览，OpenAI 只返回了部分单篇摘要。）"
        else:
            summary += "\n\n（OpenAI 调用失败，本次没有生成中文摘要，英文原摘要仍保留在每篇下方。）"
        return summary, enriched

    if failed_batches:
        missing = sum(1 for paper in papers if not summaries.get(paper.id))
        LOGGER.warning("有 %d 篇论文未能生成中文摘要（%d/%d 批失败）。", missing, failed_batches, total_batches)
        overall = f"{overall}\n\n（本次有 {missing} 篇论文未能生成中文摘要，已保留英文原摘要。）"
    return overall, enriched
