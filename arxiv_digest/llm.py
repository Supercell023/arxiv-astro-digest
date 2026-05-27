from __future__ import annotations

import json

from .models import Paper


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
) -> tuple[str, list[Paper]]:
    if not papers:
        return _fallback_summary(papers), []

    llm_papers = papers[: max(0, max_llm_papers)]
    if not api_key or not llm_papers:
        return _fallback_summary(papers), add_fallback_paper_summaries(papers)

    try:
        from openai import OpenAI
    except ImportError:
        return _fallback_summary(papers), add_fallback_paper_summaries(papers)

    try:
        if base_url:
            client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            client = OpenAI(api_key=api_key)
        paper_block = "\n\n".join(
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
            for paper in llm_papers
        )
        prompt = f"""请用中文为一位天文学/天体物理研究者整理以下 arXiv 新论文。

请只返回 JSON，不要使用 Markdown。JSON 格式必须为：
{{
  "overall_summary": "4-6 句中文总览，指出共同主题、值得注意的方法或结果，并保持谨慎",
  "paper_summaries": {{
    "论文 ID": "对应论文的 2-3 句中文单篇摘要，说明核心问题、方法/数据、主要结论或价值"
  }}
}}

要求：
1. 每篇论文都必须在 paper_summaries 中有一条中文摘要。
2. 不要夸大结论；如果只来自摘要，请保持谨慎措辞。
3. 单篇摘要面向研究者，避免泛泛而谈。

论文列表：
{paper_block}
"""

        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "你是谨慎、专业的天文学论文助理，只输出可解析 JSON。"},
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        paper_summaries = payload.get("paper_summaries", {})
        enriched = [
            _copy_paper(
                paper,
                chinese_summary=paper_summaries.get(paper.id) or _fallback_paper_summary(paper),
            )
            for paper in papers
        ]
        return payload.get("overall_summary") or _fallback_summary(papers), enriched
    except Exception as exc:
        summary = f"{_fallback_summary(papers)}\n\nOpenAI summary fallback was used because the API call failed: {exc}"
        return summary, add_fallback_paper_summaries(papers)
