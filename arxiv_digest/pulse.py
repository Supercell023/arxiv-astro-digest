"""本周脉搏：这一周，领域里哪几件事真的在动。

会议日期是"已知的日程"，这个模块回答的是另一半问题——
**大家在关注什么、在争论什么**。

它不靠模型凭空总结，而是先把本周所有材料（新论文、二手解读、会议报告）
落成可核对的"主题命中表"，再让模型在**只准引用这些材料**的前提下写判断：

1. 主题频次：领域包里写死的中英别名，在标题（权重 2）与正文（权重 1）里命中计数；
2. 跨源共现：同一主题被 ≥2 个独立信息源提到，才算"多处在说"，单源不升级为头条；
3. 张力信号：摘要里出现 tension / discrepancy / inconsistent 这类措辞的条目，
   单独标记，用来支撑"有争论"的判断；
4. 证据校验：模型产出的每一条必须挂上本周真实存在的条目 id，
   挂不上的一律丢弃——宁可少一条，不要一条编的。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .models import Paper
from .profile import Profile
from .sources.base import Item
from .llm_client import LLMOptions, call_json

LOGGER = logging.getLogger(__name__)

PROMPT_VERSION = "p1"
MAX_TOPICS = 8
MAX_THREADS = 4
MIN_TOPIC_HITS = 2
TITLE_WEIGHT = 2
TEXT_WEIGHT = 1

# 二手解读与会议报告是"别人已经筛过的信号"，权重高于新投稿。
KIND_WEIGHTS = {"secondary": 2.0, "talk": 1.5, "paper": 1.0}

_STATUS_LABELS = {"new": "新结果", "debate": "有争论", "unsettled": "尚无定论"}

_TENSION_RE = re.compile(
    r"\b(tension|discrepanc\w*|inconsisten\w*|conflict\w*|puzzle\w*|"
    r"challeng\w*|anomal\w*|unexpected\w*|troubl\w*|at odds|"
    r"tension with|under-predic\w*|over-predic\w*)\b",
    re.IGNORECASE,
)

_ALIAS_CACHE: dict[str, re.Pattern[str]] = {}

_GENERIC_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("弱透镜", ("weak lensing", "weak-lensing", "cosmic shear")),
    ("星系团", ("galaxy cluster", "cluster", "clusters")),
    ("光度红移", ("photometric redshift", "photo-z", "photoz")),
    ("BAO / RSD", ("baryon acoustic", " BAO", "redshift-space distortion", " RSD")),
    ("暗能量", ("dark energy", "w0wa", "equation of state")),
    ("引力透镜", ("lensing", "shear", "convergence")),
    ("JWST", ("jwst", "james webb")),
    ("Euclid", ("euclid",)),
    ("DESI", ("desi",)),
    ("Rubin / LSST", ("rubin", "lsst", "vera c. rubin")),
    ("模拟 / mock", ("simulation", "mock catalog", "hydrodynamical")),
    ("AGN / 反馈", ("agn", "feedback", "quenching")),
    ("再电离", ("reionization", "reionisation", "epoch of reionization")),
    ("S8 张力", ("s8", "sigma8", "s8 tension")),
    ("大尺度结构", ("large-scale structure", "power spectrum", "two-point")),
    ("H0 张力", ("hubble constant", "h0", "h0 tension")),
)


@dataclass(frozen=True)
class TopicHit:
    """一个主题在本周材料里被提到的情况。"""

    label: str
    hits: int
    sources: tuple[str, ...] = ()
    item_ids: tuple[str, ...] = ()
    tension_items: tuple[str, ...] = ()

    @property
    def source_count(self) -> int:
        return len(self.sources)

    @property
    def is_shared(self) -> bool:
        """被两个以上独立信息源提到——单源的单条不算"大家在说"。"""

        return self.source_count >= 2


@dataclass(frozen=True)
class Thread:
    """一条"大家在讨论什么"。evidence 全部是本周材料的 id。"""

    question: str
    summary: str
    status: str = "new"
    evidence: tuple[str, ...] = ()
    why_you: str = ""
    links: tuple[tuple[str, str], ...] = ()

    @property
    def status_label(self) -> str:
        return _STATUS_LABELS.get(self.status, "新结果")


@dataclass(frozen=True)
class Pulse:
    headline: str = ""
    headline_url: str = ""
    threads: tuple[Thread, ...] = ()
    topics: tuple[TopicHit, ...] = ()
    quiet: str = ""
    basis: str = "fallback"
    materials: int = 0
    source_count: int = 0

    @property
    def is_empty(self) -> bool:
        return not (self.headline or self.threads)

    @property
    def pick(self) -> Thread | None:
        """如果要只读一篇，读这条。

        挑法：证据最多的那条 thread 的第一篇——证据多说明这一周真的有人在
        就同一个问题发言，而不是孤例。没有链接就当没有推荐。
        """

        candidates = [thread for thread in self.threads if thread.links]
        if not candidates:
            return None
        return max(candidates, key=lambda thread: (len(thread.links), len(thread.summary)))

    @property
    def pick_link(self) -> tuple[str, str] | None:
        thread = self.pick
        if thread is None or not thread.links:
            return None
        return thread.links[0]


@dataclass
class Material:
    """送进提示词的一条材料。"""

    id: str
    kind: str
    title: str
    source: str
    date: str
    text: str = ""
    url: str = ""

    def render_line(self) -> str:
        body = f"- [{self.id}] {self.kind}｜{self.source}｜{self.date}｜{self.title}"
        if self.text:
            body += f"\n  {self.text[:260]}"
        return body


def materials_from(
    papers: Sequence[Paper],
    secondary: Sequence[Item] = (),
    talks: Sequence[Item] = (),
    *,
    max_papers: int = 40,
) -> list[Material]:
    """把三类材料摊平成同一张表。论文取标题+摘要，二手/报告取正文。"""

    materials: list[Material] = []
    for paper in papers[:max_papers]:
        materials.append(
            Material(
                id=paper.id,
                kind="paper",
                title=paper.title,
                # 源必须是"arXiv"而不是分类名：分类名每天都不重复，
                # 会把"跨源共现"虚化成永远成立。
                source="arXiv",
                date=str(paper.published)[:10],
                text=paper.summary,
                url=paper.abs_url,
            )
        )
    for item in secondary:
        materials.append(
            Material(
                id=item.id,
                kind="secondary",
                title=item.title,
                source=item.source,
                date=(item.published or "")[:10],
                text=item.text,
                url=item.url,
            )
        )
    for item in talks:
        materials.append(
            Material(
                id=item.id,
                kind="talk",
                title=item.title,
                source=item.source,
                date=(item.published or "")[:10],
                text=item.text,
                url=item.url,
            )
        )
    return materials


def _topic_table(topics: Iterable[tuple[str, Sequence[str]]] | None) -> list[tuple[str, list[str]]]:
    """领域包里没配就用通用表兜底，保证换领域也还能数出主题。"""

    rows: list[tuple[str, list[str]]] = []
    if topics:
        for label, aliases in topics:
            label_text = str(label or "").strip()
            alias_texts = [str(alias).strip().lower() for alias in aliases if str(alias).strip()]
            if label_text and alias_texts:
                rows.append((label_text, alias_texts))
    if rows:
        return rows
    return [(label, [alias.lower() for alias in aliases]) for label, aliases in _GENERIC_TOPICS]


def _alias_pattern(alias: str) -> re.Pattern[str] | None:
    """短别名必须按词边界匹配，否则 `desi` 会命中 `design`、`bao` 会命中 `baobab`。"""

    cleaned = alias.strip()
    if not cleaned:
        return None
    return _ALIAS_CACHE.setdefault(
        cleaned, re.compile(rf"(?<![A-Za-z0-9]){re.escape(cleaned)}(?![A-Za-z0-9])", re.IGNORECASE)
    )


def _hits_in(text: str, aliases: Sequence[str]) -> int:
    if not text:
        return 0
    total = 0
    for alias in aliases:
        pattern = _alias_pattern(alias)
        if pattern is not None:
            total += len(pattern.findall(text))
    return total


def count_topics(
    materials: Sequence[Material],
    topics: Iterable[tuple[str, Sequence[str]]] | None = None,
    *,
    limit: int = MAX_TOPICS,
    min_hits: int = MIN_TOPIC_HITS,
) -> list[TopicHit]:
    """统计主题命中；同时记下"谁在说"和"有没有张力措辞"。"""

    table = _topic_table(topics)
    hits: list[TopicHit] = []
    for label, aliases in table:
        total = 0
        sources: dict[str, None] = {}
        item_ids: list[str] = []
        tension_items: list[str] = []
        for material in materials:
            title_hits = _hits_in(material.title, aliases)
            text_hits = _hits_in(material.text, aliases) if material.text else 0
            score = (title_hits * TITLE_WEIGHT + text_hits * TEXT_WEIGHT) * KIND_WEIGHTS.get(material.kind, 1.0)
            if score <= 0:
                continue
            total += int(score)
            sources[material.source] = None
            if material.id not in item_ids:
                item_ids.append(material.id)
            if material.text and _TENSION_RE.search(material.text) and material.id not in tension_items:
                tension_items.append(material.id)
        if total >= min_hits and item_ids:
            hits.append(
                TopicHit(
                    label=label,
                    hits=total,
                    sources=tuple(list(sources)[:6]),
                    item_ids=tuple(item_ids[:12]),
                    tension_items=tuple(tension_items[:6]),
                )
            )
    hits.sort(key=lambda hit: (hit.hits, hit.source_count), reverse=True)
    return hits[:limit]


def _material_index(materials: Sequence[Material]) -> dict[str, Material]:
    return {material.id: material for material in materials}


def render_material_block(materials: Sequence[Material], *, limit: int = 30) -> str:
    return "\n".join(material.render_line() for material in materials[:limit])


def render_topic_block(topics: Sequence[TopicHit]) -> str:
    if not topics:
        return "（本周没有主题命中到阈值，可能是材料太少）"
    lines: list[str] = []
    for hit in topics:
        shared = f"，{hit.source_count} 个源均在说" if hit.is_shared else ""
        tension = f"，其中 {len(hit.tension_items)} 条带张力措辞" if hit.tension_items else ""
        lines.append(f"- {hit.label}：命中 {hit.hits} 次（{len(hit.item_ids)} 条材料{shared}{tension}）")
    return "\n".join(lines)


def build_pulse_prompt(
    profile: Profile,
    materials: Sequence[Material],
    topics: Sequence[TopicHit],
    *,
    week_label: str = "",
) -> str:
    """Stage C 提示词：只准根据给定材料写，每条必须挂 evidence。"""

    profile_block = profile.render_prompt_block() if not profile.is_empty else "（无画像，按通用天文学读者处理）"
    return f"""你在为一位研究者写"本周领域脉搏"。只准使用下面给出的材料，不得引入任何外部知识。

{profile_block}

【时间】{week_label or "本周"}

【本周材料】（方括号里是材料 id，写证据时只能用这些 id）
{render_material_block(materials)}

【程序统计出的主题命中】（已按命中强度排序；"多个源均在说"代表不是单点噪音）
{render_topic_block(topics)}

【任务】回答两件事：
1. 这一周，这个领域里**最值得知道的一件事**是什么？
2. 有哪 3-4 个问题**大家正在关注或争论**？

【硬规则】
- 每条判断必须挂 `evidence`（材料 id 列表）；没有材料支撑的判断直接不要写。
- 区分三种状态：`new`（有新结果/新数据）、`debate`（存在相互矛盾的结论）、`unsettled`（没有定论、只是被反复讨论）。
- 判断"是否在争论"时，优先看向带张力措辞的材料；只有一条材料说的事，不要写成"大家都在讨论"。
- 不要复述材料标题；读者能点链接。
- 不要写"这是一个重要领域"这类空话。
- `why_you` 只写对这位读者的具体含义（能借用什么、需要警惕什么），一句话。

【输出 JSON】
{{
  "headline": "一句话，不超过 40 字，必须能被某条材料支撑",
  "headline_evidence": ["材料 id"],
  "threads": [
    {{
      "question": "大家在问什么（问句，不超过 24 字）",
      "summary": "一两句说清目前进展与分歧点（40-90 字）",
      "status": "new|debate|unsettled",
      "evidence": ["材料 id", "材料 id"],
      "why_you": "对你意味着什么，一句话"
    }}
  ],
  "quiet": "如果明显某个方向本周没有动静，一句话说明；没有就留空字符串"
}}"""


def _chat_json(
    client: Any,
    model: str,
    prompt: str,
    *,
    options: LLMOptions | None = None,
) -> dict[str, Any]:
    return call_json(client, model, prompt, options=options)


def _as_text(value: Any, limit: int = 200) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _links_for(evidence: Sequence[str], index: Mapping[str, "Material"]) -> tuple[tuple[str, str], ...]:
    """把证据 id 变成"标题 + 链接"，让邮件里每条判断都能点回原文。"""

    links: list[tuple[str, str]] = []
    for item_id in evidence:
        material = index.get(item_id)
        if material is None or not material.url:
            continue
        links.append((material.title[:48], material.url))
    return tuple(links[:3])


def coerce_threads(
    payload: Mapping[str, Any],
    valid_ids: set[str],
    *,
    limit: int = MAX_THREADS,
    index: Mapping[str, "Material"] | None = None,
) -> list[Thread]:
    """只保留证据能对上的条目——这是这套东西不撒谎的唯一硬约束。"""

    raw_threads = payload.get("threads")
    if not isinstance(raw_threads, (list, tuple)):
        return []
    threads: list[Thread] = []
    for raw in raw_threads:
        if not isinstance(raw, dict):
            continue
        question = _as_text(raw.get("question"), 60)
        if not question:
            continue
        evidence_raw = raw.get("evidence")
        evidence = [
            str(item).strip()
            for item in (evidence_raw if isinstance(evidence_raw, (list, tuple)) else [])
            if str(item).strip() in valid_ids
        ]
        if not evidence:
            LOGGER.info("丢弃无证据的判断：%s", question)
            continue
        status = str(raw.get("status") or "new").strip().lower()
        if status not in _STATUS_LABELS:
            status = "new"
        unique = tuple(dict.fromkeys(evidence))[:4]
        threads.append(
            Thread(
                question=question,
                summary=_as_text(raw.get("summary"), 220),
                status=status,
                evidence=unique,
                why_you=_as_text(raw.get("why_you"), 120),
                links=_links_for(unique, index or {}),
            )
        )
        if len(threads) >= limit:
            break
    return threads


def coerce_pulse(
    payload: Mapping[str, Any],
    materials: Sequence[Material],
    topics: Sequence[TopicHit],
    *,
    limit: int = MAX_THREADS,
) -> Pulse:
    index = _material_index(materials)
    valid = set(index)
    threads = coerce_threads(payload, valid, limit=limit, index=index)

    headline = _as_text(payload.get("headline"), 80)
    headline_ids = [
        str(item).strip()
        for item in (payload.get("headline_evidence") or [])
        if str(item).strip() in valid
    ]
    headline_url = index[headline_ids[0]].url if headline_ids else ""
    if headline_ids and not headline:
        headline = index[headline_ids[0]].title

    sources = {material.source for material in materials}
    if not headline and not threads:
        return fallback_pulse(materials, topics)

    return Pulse(
        headline=headline,
        headline_url=headline_url,
        threads=tuple(threads),
        topics=tuple(topics),
        quiet=_as_text(payload.get("quiet"), 120),
        basis="llm",
        materials=len(materials),
        source_count=len(sources),
    )


def fallback_pulse(
    materials: Sequence[Material],
    topics: Sequence[TopicHit],
    *,
    limit: int = 3,
) -> Pulse:
    """没有模型时的诚实版：只陈述"哪几个主题被提到最多"，不做判断。"""

    if not topics:
        return Pulse(materials=len(materials), source_count=len({m.source for m in materials}))

    index = _material_index(materials)
    threads: list[Thread] = []
    for hit in topics[:limit]:
        evidence = [item_id for item_id in hit.item_ids if item_id in index][:3]
        if not evidence:
            continue
        titles = "；".join(index[item_id].title[:60] for item_id in evidence)
        threads.append(
            Thread(
                question=f"{hit.label} 本周被提到 {hit.hits} 次",
                summary=f"涉及 {len(hit.item_ids)} 条材料：{titles}",
                status="debate" if hit.tension_items else "new",
                evidence=tuple(evidence),
                links=_links_for(evidence, index),
            )
        )
    top = topics[0]
    return Pulse(
        headline=f"本周最集中的主题是 {top.label}（{top.hits} 次提及，{top.source_count} 个源）",
        headline_url=index[top.item_ids[0]].url if top.item_ids and top.item_ids[0] in index else "",
        threads=tuple(threads),
        topics=tuple(topics),
        basis="fallback",
        materials=len(materials),
        source_count=len({m.source for m in materials}),
    )


def compose_pulse(
    client: Any,
    model: str,
    materials: Sequence[Material],
    topics: Sequence[TopicHit],
    profile: Profile,
    *,
    week_label: str = "",
    limit: int = MAX_THREADS,
    options: LLMOptions | None = None,
) -> Pulse:
    """跑 Stage C；任何失败都退回 fallback（只数数，不编判断）。"""

    if not materials or not topics:
        return fallback_pulse(materials, topics)
    prompt = build_pulse_prompt(profile, materials, topics, week_label=week_label)
    try:
        payload = _chat_json(client, model, prompt, options=options)
    except Exception as exc:
        LOGGER.warning("脉搏生成失败：%s，退回主题统计。", exc)
        return fallback_pulse(materials, topics)
    return coerce_pulse(payload, materials, topics, limit=limit)


def format_plain_text(pulse: Pulse) -> str:
    if pulse.is_empty:
        return "（本周没有足够的材料形成脉搏）"
    lines = [f"头条：{pulse.headline}"]
    for thread in pulse.threads:
        lines.append(f"- [{thread.status_label}] {thread.question}")
        if thread.summary:
            lines.append(f"  {thread.summary}")
        if thread.why_you:
            lines.append(f"  对你：{thread.why_you}")
    if pulse.quiet:
        lines.append(f"安静的方向：{pulse.quiet}")
    lines.append(f"（基于 {pulse.materials} 条材料，来自 {pulse.source_count} 个源；依据：{pulse.basis}）")
    return "\n".join(lines)


def pulse_from_payload(payload: Mapping[str, Any] | None) -> Pulse | None:
    """从 replay JSON 还原，用于离线渲染预览。"""

    if not isinstance(payload, Mapping):
        return None
    threads = tuple(
        Thread(
            question=_as_text(raw.get("question"), 60),
            summary=_as_text(raw.get("summary"), 220),
            status=str(raw.get("status") or "new"),
            evidence=tuple(str(item) for item in (raw.get("evidence") or [])),
            why_you=_as_text(raw.get("why_you"), 120),
            links=tuple(
                (str(pair[0]), str(pair[1]))
                for pair in (raw.get("links") or [])
                if isinstance(pair, (list, tuple)) and len(pair) >= 2
            ),
        )
        for raw in (payload.get("threads") or [])
        if isinstance(raw, Mapping) and raw.get("question")
    )
    topics = tuple(
        TopicHit(
            label=_as_text(raw.get("label"), 40),
            hits=int(raw.get("hits") or 0),
            sources=tuple(str(item) for item in (raw.get("sources") or [])),
            item_ids=tuple(str(item) for item in (raw.get("item_ids") or [])),
            tension_items=tuple(str(item) for item in (raw.get("tension_items") or [])),
        )
        for raw in (payload.get("topics") or [])
        if isinstance(raw, Mapping) and raw.get("label")
    )
    return Pulse(
        headline=_as_text(payload.get("headline"), 80),
        headline_url=str(payload.get("headline_url") or ""),
        threads=threads,
        topics=topics,
        quiet=_as_text(payload.get("quiet"), 120),
        basis=str(payload.get("basis") or "recorded"),
        materials=int(payload.get("materials") or 0),
        source_count=int(payload.get("source_count") or 0),
    )


@dataclass
class PulseInput:
    """一次性把原料备齐，方便调用方与预览脚本复用。"""

    materials: list[Material] = field(default_factory=list)
    topics: list[TopicHit] = field(default_factory=list)
