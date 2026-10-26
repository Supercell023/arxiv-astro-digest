"""二手解读的中文翻译。

二手源（AAS Nova / ESO / NASA / 会议报告）都是英文，直接摆进中文邮件里读起来很割裂。
这里只做一件事：把标题和 2-3 句摘要译成中文，并**保留英文原标题**作为核对锚点。

硬规则：
1. 只译不改——不添加原文没有的结论，数字照抄；
2. 术语保留英文（BAO、HOD、photometric redshift 之类），因为中文译名在领域里并不通用；
3. 失败就退回原文，不让整段消失；
4. 分批翻译——一次译太多条会被 max_tokens 截断，截断的那一批整体作废，
   表现出来就是"前 12 条有中文、后面全是英文"。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Sequence

from .llm_client import LLMOptions, call_json
from .sources import Item
from .evidence import numbers_supported

LOGGER = logging.getLogger(__name__)

MAX_ITEMS = 40
BATCH_SIZE = 8


def build_translate_prompt(items: Sequence[Item]) -> str:
    lines = [
        "把下面每条科学或历史新闻译成中文。读者是天文学研究生，也关注物理、化学与历史考古；按原领域翻译，不要把它们强行解释成天文学。",
        "",
    ]
    for index, item in enumerate(items, start=1):
        lines.append(f"[{index}] id={item.id}")
        lines.append(f"    标题：{item.title}")
        body = (item.text or "").strip()
        if body:
            lines.append(f"    正文：{body[:900]}")
    lines.append("")
    lines.append("要求：")
    lines.append("1. 标题译成中文（不超过 24 字），专业名词可保留英文；")
    lines.append("2. 正文译成 150-220 字中文（3-4 句），按这个顺序写：")
    lines.append("   ① 这件事是什么、为什么现在值得说；② 他们做了什么（数据、方法、关键数字）；")
    lines.append("   ③ 结论是什么、边界在哪；④ 对这个领域意味着什么；")
    lines.append("3. 数字、专名、年代与编号照抄，不要换算；不要把历史报道写成实验结果，不要补充原文没有的结论；")
    lines.append("4. 原文信息不足以支撑 120 字时，写到多少算多少，正文可以短，但不要编造细节、不要用套话凑长度。")
    lines.append("")
    lines.append('输出 JSON：{"items": [{"id": "…", "title_zh": "…", "text_zh": "…"}]}')
    return "\n".join(lines)


def _parse_mapping(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return {}
    mapping: dict[str, dict[str, str]] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("id") or "").strip()
        if not key:
            continue
        mapping[key] = {
            "title_zh": str(raw.get("title_zh") or "").strip(),
            "text_zh": str(raw.get("text_zh") or "").strip(),
        }
    return mapping


def translate_items(
    client: Any,
    model: str,
    items: Sequence[Item],
    *,
    limit: int = MAX_ITEMS,
    batch_size: int = BATCH_SIZE,
    options: LLMOptions | None = None,
) -> list[Item]:
    """把条目翻成中文；任何一批失败都原样返回那一批，不抛异常。"""

    if not items:
        return []

    targets = list(items)[: max(0, limit)]
    if not targets:
        return list(items)
    opts = options or LLMOptions(temperature=0.3, max_tokens=3000)
    step = max(1, batch_size)
    batches = [targets[i : i + step] for i in range(0, len(targets), step)]

    mapping: dict[str, dict[str, str]] = {}
    for index, batch in enumerate(batches, start=1):
        try:
            payload = call_json(
                client,
                model,
                build_translate_prompt(batch),
                options=opts,
            )
        except Exception as exc:  # noqa: BLE001 - 翻译失败不该影响发信
            LOGGER.warning(
                "第 %d/%d 批二手解读翻译失败，保留英文原文：%s",
                index,
                len(batches),
                exc,
            )
            continue
        mapping.update(_parse_mapping(payload))

    translated: list[Item] = []
    for item in items:
        patch = mapping.get(item.id)
        if not patch:
            translated.append(item)
            continue
        translated.append(
            replace(
                item,
                title_zh=(patch["title_zh"] or item.title_zh) if numbers_supported(patch["title_zh"], item.title + " " + item.text) else "",
                text_zh=(patch["text_zh"] or item.text_zh) if numbers_supported(patch["text_zh"], item.title + " " + item.text) else "",
            )
        )
    return translated
