from __future__ import annotations

import json
import logging
import re
from typing import Any

from .models import Paper
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
        return "前一天没有找到符合当前配置的新 arXiv 论文。"
    top_titles = "；".join(paper.title for paper in papers[:3])
    return f"共筛选出 {len(papers)} 篇论文。优先关注：{top_titles}。"


def _fallback_paper_summary(paper: Paper) -> str:
    return f"这篇论文的英文摘要要点：{paper.summary}"


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


def _build_prompt(paper_block: str, *, ask_overall: bool) -> str:
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
    return f"""请用中文为一位天文学/天体物理研究者整理以下 arXiv 新论文。

请只返回 JSON，不要使用 Markdown。JSON 格式必须为：
{schema}

要求：
1. 每篇论文都必须在 paper_summaries 中有一条中文摘要。
2. 不要夸大结论；如果只来自摘要，请保持谨慎措辞。
3. 单篇摘要面向研究者，避免泛泛而谈。{extra}

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
) -> tuple[str | None, dict[str, str]]:
    prompt = _build_prompt(_build_paper_block(batch, max_abstract_chars), ask_overall=ask_overall)
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
        summaries = {
            str(key): str(value).strip()
            for key, value in raw_summaries.items()
            if isinstance(value, str) and value.strip()
        }

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
            client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            client = OpenAI(api_key=api_key)
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
                ask_overall=overall is None,
                max_abstract_chars=max_abstract_chars,
                thinking=thinking,
            )
        except Exception as exc:
            failed_batches += 1
            LOGGER.warning("第 %d/%d 批（%d 篇）生成失败：%s", index, total_batches, len(batch), exc)
            continue
        if overall is None and batch_overall:
            overall = batch_overall
        summaries.update(batch_summaries)

    enriched = [
        _copy_paper(
            paper,
            chinese_summary=summaries.get(paper.id) or paper.chinese_summary or _fallback_paper_summary(paper),
        )
        for paper in papers
    ]

    if overall is None:
        summary = _fallback_summary(papers)
        if failed_batches < total_batches:
            summary += "\n\n（本次未能生成总览，OpenAI 只返回了部分单篇摘要。）"
        else:
            summary += "\n\n（OpenAI 调用失败，全部论文已降级为英文摘要要点。）"
        return summary, enriched

    if failed_batches:
        missing = sum(1 for paper in papers if not summaries.get(paper.id))
        LOGGER.warning("有 %d 篇论文未能生成中文摘要（%d/%d 批失败）。", missing, failed_batches, total_batches)
        overall = f"{overall}\n\n（本次有 {missing} 篇论文未能生成中文摘要，已保留英文要点。）"
    return overall, enriched
