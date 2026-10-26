"""两阶段分析：先把摘要拆成事实，再按读者画像写评论。

为什么拆两步，而不是让模型"读一遍摘要直接写七段评论"：

1. **可核对**：Stage A 每抽一条事实，必须附一句原文 quote。数字对不上原文，
   在 Stage A 就露馅了，而不是等到 Stage B 写进评论里没人能查。
2. **可降级**：摘要没写样本量，Stage A 就返回 null，Stage B 只能写"摘要未提"，
   它没有机会"合理推测"一个 120,000 个星系。
3. **可缓存**：Stage A 只依赖 (论文, 提示词版本)，换模型重写评论时不必重抽。

两个阶段的客户端都是注入的，因此可以脱离网络用假客户端写测试（见 selfcheck）。
"""

from __future__ import annotations

import logging
import hashlib
import json
import re
from pathlib import Path
from .evidence import quote_in_source, numbers_supported, advice_supported
import math
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence

from .fulltext import extract_figure_captions
from .models import Analysis, Concept, Fact, KeyNumber, Paper, RedFlag, ZoteroItem
from .packs import Pack, dispatch
from .profile import Profile
from .llm_client import LLMOptions, call_json
from .storage import atomic_json

LOGGER = logging.getLogger(__name__)

PROMPT_VERSION = "a4-b2-validated"
FACT_KEYS: tuple[str, ...] = ("data", "processing", "systematics", "method", "result", "caveat")
# motivation 只做背景模块的原料，不进七问面板（七问是"这篇做了什么"，背景是"为什么做"）
EXTRACT_KEYS: tuple[str, ...] = FACT_KEYS + ("motivation",)
# 数值回查失败时按分句降级：只丢含不可回查数字的那半句，不整段清空。
_CLAUSE_SPLIT = re.compile(r"[^。！？；;]+[。！？；;]?")
MAX_TEXT_CHARS = 2200
MAX_FULLTEXT_PROMPT_CHARS = 36_000
MAX_NUMBERS = 6
MAX_CONCEPTS = 4
MISSING = "摘要未提"
MISSING_FULLTEXT = "全文节选未检出"

_SYSTEM_EXTRACT = "你是严谨的论文信息抽取器。只抽取原文明确写出的内容，不推断、不补充领域常识。"
_SYSTEM_ASSESS = "你是谨慎的领域评审助手。所有判断必须能追溯到给定事实；读不到就明说，不许编。"

_EXTRACT_SCHEMA = """{
  "data":        {"value": "中文概括，如：SDSS DR7 光谱样本，约 4.2 万个星系团，0.05<z<0.3", "quote": "原文中的连续英文原句"},
  "processing":  {"value": "...", "quote": "..."},
  "systematics": {"value": "...", "quote": "..."},
  "method":      {"value": "...", "quote": "..."},
  "result":      {"value": "...", "quote": "..."},
  "caveat":      {"value": "...", "quote": "..."},
  "motivation":  {"value": "这项研究为什么值得做：原文点明的空白、矛盾或未检验的假设", "quote": "原文中的连续英文原句"},
  "numbers": [
    {"label": "样本量", "value": "4.2 万星系团", "quote": "原文原句"},
    {"label": "红移范围", "value": "0.05 < z < 0.3", "quote": "原文原句"}
  ],
  "concepts": [
    {"term": "miscentering", "gloss": "一句话中文解释（≤30 字）"}
  ]
}"""

_ASSESS_SCHEMA = """{
  "relevance": 0.0,
  "relevance_kind": "direct|method|data|formation|context|off",
  "relevance_reason": "一句话，说明为什么是这个相关度",
  "verdict": "一句话结论（≤40 字）：这篇做了什么、结果是什么",
  "read_advice": "read|skim|archive",
  "read_reason": "为什么建议这样读（≤30 字）",
  "background": "研究背景：这个方向现在卡在哪、本文要回答什么（≤220 字）",
  "method_detail": "方法详解：数据怎么处理、估计量怎么定义、拟合与协方差怎么设（≤360 字）",
  "data": "...", "processing": "...", "systematics": "...",
  "method": "...", "result": "...", "caveat": "...", "for_you": "...",
  "chain": ["数据/样本", "切选与完备性", "误差与标定", "估计量/统计量", "拟合设定", "结论"],
  "actions": ["具体动作 1", "具体动作 2", "具体动作 3"],
  "red_flags": [{"flag": "领域包里的某条红旗", "status": "discussed|partial|missing", "note": "一句话"}],
  "concepts": [{"term": "术语", "gloss": "一句话解释"}],
  "confidence": "high|medium|low",
  "evidence": ["原文里支撑结论的英文原句", "..."],
  "basis": "abstract|fulltext"
}"""


def _chat_json(
    client: Any,
    model: str,
    system: str,
    user: str,
    *,
    options: LLMOptions | None = None,
) -> dict[str, Any]:
    return call_json(client, model, user, system=system, options=options)


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …"


def _text(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    return default


# —— Stage A：抽事实 ——


def build_extract_prompt(paper: Paper, *, fulltext: str | None = None) -> str:
    if fulltext:
        source_label = "全文节选"
        source_note = (
            "下面是论文全文的节选：去掉了参考文献，保留开头与含样本/筛选/系统误差关键词的句子。\n"
            "图表数字与公式可能缺失，缺失的就当原文没提。"
        )
        source_text = _clip(fulltext, MAX_FULLTEXT_PROMPT_CHARS)
    else:
        source_label = "摘要"
        source_note = "下面只有摘要。摘要通常不写样本筛选与误差校正，读不到就是没提。"
        source_text = _clip(paper.summary)

    return f"""从下面这篇论文的{source_label}中抽取信息。

只抽取原文明确写出的内容；某个字段原文没提，就把该字段整条设为 null。
不要用领域常识补全，不要把"通常如此"写成事实。
{source_note}

JSON 输出格式：
{_EXTRACT_SCHEMA}

字段含义：
- data：数据集类型（survey / simulation / mock）、样本量、红移与质量区间
- processing：关键 cut、完备性、掩膜、成员分配等筛选处理步骤
- systematics：系统误差来源与校正手段
- method：估计量、建模、拟合设定、协方差
- result：主要结果，含关键数字
- caveat：作者自己承认的局限
- motivation：这项研究为什么值得做。要抓的是原文引子里那句"已知哪里不够"，
  比如"不同巡天给出的 SZ 质量标定相差 20%，尚未判断是测量误差还是物理"。
  只写原文说了的动机，不要替作者写领域综述。
- numbers：从原文里摘出 3-6 个**能被独立引用的数**（样本量、红移范围、拟合区间、
  误差棒、显著性、偏差幅度）。label 用中文短语（如"样本量""红移范围""质量偏差"），
  value 保留原有单位与量级，quote 必须是原文原句。数不出来就返回空数组。
- concepts：原文出现、而读者画像词表里没有的生僻术语，2-4 条。gloss 一句话说清它
  在这篇论文里指什么，不要写成教科书定义。

value 用中文概括：一般字段不超过 90 字，method 与 motivation 可以到 200 字
（方法细节和动机常常一句话说不完，宁肯长一点也不要丢条件）。
quote 必须是原文中的连续英文片段（不超过 200 字符）。

标题：{paper.title}
分类：{", ".join(paper.categories)}
{source_label}：
{source_text}
"""


def _coerce_fact(raw: Any) -> Fact:
    if isinstance(raw, dict):
        return Fact(value=_text(raw.get("value")) or None, quote=_text(raw.get("quote")) or None)
    text = _text(raw)
    return Fact(value=text or None, quote=None)


def coerce_facts(payload: Mapping[str, Any]) -> dict[str, Fact]:
    return {key: _coerce_fact(payload.get(key)) for key in EXTRACT_KEYS}


def coerce_numbers(payload: Mapping[str, Any], *, limit: int = MAX_NUMBERS) -> tuple[KeyNumber, ...]:
    raw = payload.get("numbers")
    if not isinstance(raw, (list, tuple)):
        return ()
    numbers: list[KeyNumber] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        label = _text(item.get("label"))
        value = _text(item.get("value"))
        if not label or not value:
            continue
        numbers.append(KeyNumber(label=label, value=value, quote=_text(item.get("quote")) or None))
        if len(numbers) >= limit:
            break
    return tuple(numbers)


def coerce_concepts(
    payload: Mapping[str, Any], *, limit: int = MAX_CONCEPTS
) -> tuple[Concept, ...]:
    raw = payload.get("concepts")
    if not isinstance(raw, (list, tuple)):
        return ()
    concepts: list[Concept] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        term = _text(item.get("term"))
        gloss = _text(item.get("gloss"))
        if not term or not gloss:
            continue
        concepts.append(Concept(term=term, gloss=gloss))
        if len(concepts) >= limit:
            break
    return tuple(concepts)


@dataclass(frozen=True)
class ExtractBundle:
    """Stage A 的全部产出：六条事实 + 关键数字 + 术语。"""

    facts: dict[str, Fact]
    numbers: tuple[KeyNumber, ...] = ()
    concepts: tuple[Concept, ...] = ()


def extract_bundle(
    client: Any,
    model: str,
    paper: Paper,
    *,
    fulltext: str | None = None,
    options: LLMOptions | None = None,
    cache_dir: str = "",
) -> ExtractBundle:
    source = _clip(fulltext, MAX_FULLTEXT_PROMPT_CHARS) if fulltext else _clip(paper.summary)
    prompt = build_extract_prompt(paper, fulltext=fulltext)
    # 缓存键必须带上调用形状（thinking/temperature/max_tokens），否则改了开关仍会命中旧产物。
    option_key = json.dumps(asdict(options), sort_keys=True, ensure_ascii=False) if options else ""
    cache_key = hashlib.sha256((PROMPT_VERSION + model + option_key + prompt).encode("utf-8")).hexdigest()
    cache_path = Path(cache_dir) / "extract" / (cache_key + ".json") if cache_dir else None
    payload = None
    if cache_path and cache_path.is_file():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, ValueError):
            pass
    if payload is None:
        payload = _chat_json(client, model, _SYSTEM_EXTRACT, prompt, options=options)
    facts = {key: fact if quote_in_source(fact.quote, source) and numbers_supported(fact.value, fact.quote or "") else Fact()
             for key, fact in coerce_facts(payload).items()}
    numbers = tuple(number for number in coerce_numbers(payload)
                    if quote_in_source(number.quote, source)
                    and numbers_supported(number.label + " " + number.value, number.quote or ""))
    if cache_path and (any(not fact.is_missing for fact in facts.values()) or numbers):
        try:
            atomic_json(cache_path, payload)
        except OSError:
            LOGGER.warning("Stage A 缓存写入失败，继续本次分析。")
    missing = [key for key, fact in facts.items() if fact.is_missing]
    if missing:
        LOGGER.debug("%s 未能抽取：%s", paper.id, ", ".join(missing))
    return ExtractBundle(
        facts=facts,
        numbers=numbers,
        concepts=coerce_concepts(payload),
    )


def extract_facts(
    client: Any,
    model: str,
    paper: Paper,
    *,
    fulltext: str | None = None,
) -> dict[str, Fact]:
    return extract_bundle(client, model, paper, fulltext=fulltext).facts


# —— Stage B：按画像写评论 ——


def _facts_block(facts: Mapping[str, Fact]) -> str:
    lines: list[str] = []
    for key in EXTRACT_KEYS:
        fact = facts.get(key)
        if fact is None or fact.is_missing:
            lines.append(f"- {key}: null（原文未提）")
            continue
        quote = f"  ← \"{fact.quote}\"" if fact.quote else ""
        lines.append(f"- {key}: {fact.value}{quote}")
    return "\n".join(lines)


def _library_block(items: Sequence[ZoteroItem], *, limit: int = 24) -> str:
    """把读者的 Zotero 库压成一段短清单，给 Stage B 当"我已经有什么"的坐标。"""

    if not items:
        return "（未配置 Zotero 或库为空）"
    lines: list[str] = []
    for item in items[:limit]:
        tags = f"（{', '.join(item.tags)}）" if item.tags else ""
        lines.append(f"- {item.title[:90]}{tags}")
    if len(items) > limit:
        lines.append(f"- ……另有 {len(items) - limit} 条")
    return "\n".join(lines)


def build_assess_prompt(
    paper: Paper,
    facts: Mapping[str, Fact],
    profile: Profile,
    pack: Pack | None,
    *,
    depth_deep: str = "",
    basis: str = "abstract",
    numbers: Sequence[KeyNumber] = (),
    figures: Sequence[str] = (),
    library: Sequence[ZoteroItem] = (),
) -> str:
    pack_block = pack.render_prompt_block() if pack is not None else "领域：未归类（判断能力低，请标注）"
    depth = depth_deep or profile.depth_deep
    kinds = "、".join(profile.relevance_kind_keys) or "direct/method/data/formation/context/off"
    missing_label = MISSING_FULLTEXT if basis == "fulltext" else MISSING
    caveat_rule = (
        "caveat 必须用领域包的 red_flags 反查：作者没讨论到的坑，写成\"未见讨论：xxx\"。"
        if pack is not None and pack.red_flags
        else "caveat 只写作者自己承认的局限。"
    )
    numbers_block = (
        "\n".join(f"- {item.label}：{item.value}" for item in numbers) if numbers else "（原文没给出可独立引用的数）"
    )
    figures_block = "\n".join(f"- {caption}" for caption in figures) if figures else "（无）"
    red_flag_list = (
        "\n".join(f"- {flag}" for flag in pack.red_flags) if pack is not None and pack.red_flags else "（本领域无预置红旗）"
    )
    return f"""下面是一篇论文的已抽取事实。请按读者的画像给出结构化评论。

【读者画像】
{profile.render_prompt_block()}

【论文所属领域】
{pack_block}

【读者已收藏（Zotero 库）】
{_library_block(library)}

【论文】
标题：{paper.title}
分类：{", ".join(paper.categories) or "（非 arXiv，期刊直投）"}
期刊：{paper.venue or "arXiv 预印本"}
发表：{paper.published:%Y-%m-%d}

【已抽取事实（唯一可用信息源）】
{_facts_block(facts)}

【关键数字（原文摘出）】
{numbers_block}

【图注（从 PDF 自动提取）】
{figures_block}

【本领域预置红旗】
{red_flag_list}

【硬规则】
1. 只能用上面的事实。事实为 null 的字段，正文写"{missing_label}"，不许推测。
2. 任何数字、样本量、红移区间、误差值，必须能在 facts 的 quote 或【关键数字】里找到；找不到就不写。
3. 区分"原文里说了"和"作者没讨论"：后者写"未见讨论"或"{missing_label}"。
4. relevance 衡量的是"对这位读者是否有用"，不是论文质量。
5. for_you 必须是一个具体动作（"可以把它的质量标定流程用到 XX"），不许写"值得关注"。
6. 每条正文 {depth}。
7. **不要为阅读时长删内容**。这是存档文档，读者会按需翻，不是通勤路上读完的摘要。
   背景与方法这两块宁长勿短，唯一的上限是"每句都要带信息"，不要写"具有重要意义"这类空话。
8. 如果本文与【读者已收藏】里某条高度重合（同一对象、同一测量、只是样本更大），
   在 for_you 或 caveat 里直接点名"与已收藏的《…》重复/增量"，不要当成全新方向推荐。
{caveat_rule}

【relevance_kind 取值】{kinds}

【要额外产出的分区】
- verdict：一句话说清"这篇做了什么、结果是什么"，≤40 字。写在卡片最上方，读者只读这一句也能决定要不要点开。
- read_advice + read_reason：read（值得今天花 3 分钟读）/ skim（看图表与方法即可）/ archive（留档，别今天读）。
- background：研究背景，≤220 字，回答"为什么要有这篇"。顺序：这个方向已知什么 → 卡在哪（矛盾/未检验假设/样本限制）
  → 本文具体补哪一块。用 motivation 事实作骨架，可以补一两句领域坐标（如"同类测量在 XX 上差 2σ"），
  但坐标必须能从 facts 里推出来，不能引入新数字。不要写教科书式定义（读者是博士生）。
- method_detail：方法详解，≤360 字。比七问里的"方法"更具体：样本怎么选的、关键量怎么估的、
  拟合的自由参数与先验、协方差/似然怎么搭的、有没有做 null test 或注入测试。
  细节只能来自 facts；facts 里没有的环节就跳过，不要用"通常采用标准流程"填字数。
- chain：方法链条 5-8 步，每步一个短语（如"DES Y3 弱透镜 ΔΣ"→"richness 分箱"→"miscentering 建模"→"联合拟合"→"γ_cm 约束"）。
  目的：让人 3 秒看懂这篇的骨架，而不是读完七问才知道它怎么做的。
- actions：3 条**今天就能做**的动作（复用的流程、能对比的数据、能引用的结论），动宾结构，不要写"关注"。
  行动数量如需自拟，使用“建议抽查 3 篇”这样的明确建议；论文的测量/样本数字仍只能来自 facts。
- red_flags：把【本领域预置红旗】逐条反查，status 只能是 discussed / partial / missing；note 一句话说明依据。
  红旗一条都没踩也要照实写 discussed。
- concepts：原文出现、读者画像词表外的术语 2-4 条，gloss 说明它在这篇里指什么。

【打分锚点】
0.85-1.00 直接用读者的主探针做宇宙学，方法可搬
0.70-0.90 新数据或 mock 公开，能直接用
0.60-0.85 方法/标定可迁移到读者流程
0.50-0.75 形成演化机制类结果
0.30-0.50 值得知道但暂时用不上
0.00-0.20 无关

【输出 JSON】
{_ASSESS_SCHEMA}
"""


def _clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number)) if math.isfinite(number) else low


def _confidence(value: Any) -> str:
    text = _text(value).lower()
    return text if text in {"high", "medium", "low"} else "low"


def coerce_analysis(
    payload: Mapping[str, Any],
    *,
    profile: Profile,
    model: str = "",
    basis: str = "abstract",
    numbers: Sequence[KeyNumber] = (),
    figures: Sequence[str] = (),
) -> Analysis:
    allowed = set(profile.relevance_kind_keys) or {"direct", "method", "data", "formation", "context", "off"}
    kind = _text(payload.get("relevance_kind")).lower()
    if kind not in allowed:
        kind = "context" if "context" in allowed else sorted(allowed)[0]

    evidence_raw = payload.get("evidence")
    evidence: tuple[str, ...] = ()
    if isinstance(evidence_raw, (list, tuple)):
        evidence = tuple(text for item in evidence_raw if (text := _text(item)))

    basis_text = basis  # input provenance belongs to the program, never the model

    missing_label = MISSING_FULLTEXT if basis_text == "fulltext" else MISSING

    def field(key: str) -> str:
        return _text(payload.get(key)) or missing_label

    def string_tuple(key: str, limit: int) -> tuple[str, ...]:
        raw = payload.get(key)
        if not isinstance(raw, (list, tuple)):
            return ()
        items = [text for item in raw if (text := _text(item))]
        return tuple(items[:limit])

    advice = _text(payload.get("read_advice")).lower()
    if advice not in {"read", "skim", "archive"}:
        advice = "skim" if _clamp(payload.get("relevance")) >= 0.5 else "archive"

    red_flags: list[RedFlag] = []
    raw_flags = payload.get("red_flags")
    if isinstance(raw_flags, (list, tuple)):
        for item in raw_flags:
            if not isinstance(item, Mapping):
                continue
            flag = _text(item.get("flag"))
            if not flag:
                continue
            status = _text(item.get("status")).lower()
            if status not in {"discussed", "partial", "missing"}:
                status = "missing"
            red_flags.append(RedFlag(flag=flag, status=status, note=_text(item.get("note"))))

    return Analysis(
        relevance=_clamp(payload.get("relevance")),
        relevance_kind=kind,
        relevance_reason=_text(payload.get("relevance_reason")),
        data=field("data"),
        processing=field("processing"),
        systematics=field("systematics"),
        method=field("method"),
        result=field("result"),
        caveat=field("caveat"),
        for_you=_text(payload.get("for_you")) or "暂无明确可用点",
        confidence=_confidence(payload.get("confidence")),
        evidence=evidence,
        basis=basis_text,
        model=model,
        prompt_version=PROMPT_VERSION,
        verdict=_text(payload.get("verdict")),
        read_advice=advice,
        read_reason=_text(payload.get("read_reason")),
        background=_text(payload.get("background")),
        method_detail=_text(payload.get("method_detail")),
        chain=string_tuple("chain", 8),
        numbers=tuple(numbers),
        figures=tuple(figures),
        actions=string_tuple("actions", 3),
        red_flags=tuple(red_flags),
        concepts=coerce_concepts(payload),
    )


def _salvage_text(value: str, source: str) -> str:
    """删掉含不可回查数字的分句，保留其余分句。

    数值护栏不该让整段消失：一句话里只有一个数字对不上时，丢掉那半句
    比让读者看不到"研究背景/一句话结论"更合适。
    """
    if not value:
        return ""
    kept = [
        clause
        for clause in _CLAUSE_SPLIT.findall(value)
        if clause.strip() and numbers_supported(clause, source)
    ]
    return "".join(kept).strip()


def assess(
    client: Any,
    model: str,
    paper: Paper,
    facts: Mapping[str, Fact],
    profile: Profile,
    pack: Pack | None,
    *,
    basis: str = "abstract",
    numbers: Sequence[KeyNumber] = (),
    figures: Sequence[str] = (),
    library: Sequence[ZoteroItem] = (),
    options: LLMOptions | None = None,
    source_text: str = "",
) -> Analysis:
    prompt = build_assess_prompt(
        paper,
        facts,
        profile,
        pack,
        basis=basis,
        numbers=numbers,
        figures=figures,
        library=library,
    )
    payload = _chat_json(client, model, _SYSTEM_ASSESS, prompt, options=options)
    analysis = coerce_analysis(
        payload,
        profile=profile,
        model=model,
        basis=basis,
        numbers=numbers,
        figures=figures,
    )
    values = {key: getattr(analysis, key) if not facts.get(key, Fact()).is_missing
              else (MISSING_FULLTEXT if basis == "fulltext" else MISSING)
              for key in FACT_KEYS}
    supported_text = " ".join([fact.quote or "" for fact in facts.values()]
                              + [number.quote or "" for number in numbers] + list(figures))
    for key in (*FACT_KEYS, "background", "method_detail", "verdict"):
        value = values.get(key, getattr(analysis, key))
        if numbers_supported(value, supported_text):
            continue
        salvaged = _salvage_text(value, supported_text)
        if len(salvaged) >= 8:
            LOGGER.warning("省略含不可回查数字的分句：%s", key)
            values[key] = salvaged
        else:
            LOGGER.warning("过滤评论中数字无法回查的字段：%s", key)
            values[key] = MISSING_FULLTEXT if basis == "fulltext" else MISSING
    background_value = values.get("background", analysis.background)
    if facts.get("motivation", Fact()).is_missing and not background_value:
        values["background"] = "原文未提供可核对的研究动机"
    if values.get("verdict") in (MISSING, MISSING_FULLTEXT):
        fallback = values.get("result") or analysis.result
        if fallback and not fallback.startswith(("摘要未提", "全文未提")):
            values["verdict"] = fallback[:60]
    for key in ("for_you", "read_reason", "relevance_reason"):
        value = getattr(analysis, key)
        if not advice_supported(value, supported_text):
            values[key] = "未生成可核对的建议" if key == "for_you" else ""
    values["actions"] = tuple(value for value in analysis.actions if advice_supported(value, supported_text))
    values["chain"] = tuple(value for value in analysis.chain if numbers_supported(value, supported_text))
    values["red_flags"] = tuple(flag for flag in analysis.red_flags
        if numbers_supported(flag.note, supported_text)
        and ((pack is not None and flag.flag in pack.red_flags) or numbers_supported(flag.flag, supported_text)))
    values["concepts"] = tuple(concept for concept in analysis.concepts
                                if numbers_supported(concept.term + " " + concept.gloss, source_text))
    evidence = tuple(quote for quote in analysis.evidence if quote_in_source(quote, source_text))
    return replace(analysis, **values, evidence=evidence, facts=dict(facts), source_text=source_text,
                   source_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                   source_scope="excerpt" if basis == "fulltext" else "abstract",
                   confidence=analysis.confidence if evidence else "low")


# —— 编排 ——


def _copy_paper(paper: Paper, **updates: Any) -> Paper:
    return Paper(**{**paper.__dict__, **updates})


def analyze_paper(
    client: Any,
    paper: Paper,
    profile: Profile,
    packs: Sequence[Pack],
    *,
    extract_model: str,
    assess_model: str,
    fulltext: str | None = None,
    library: Sequence[ZoteroItem] = (),
    extract_options: LLMOptions | None = None,
    assess_options: LLMOptions | None = None,
    cache_dir: str = "",
) -> Paper:
    pack = dispatch(packs, paper.categories, hint=paper.pack_hint, title=paper.title)
    basis = "fulltext" if fulltext else "abstract"
    bundle = extract_bundle(client, extract_model, paper, fulltext=fulltext, options=extract_options, cache_dir=cache_dir)
    figures = extract_figure_captions(fulltext) if fulltext else ()
    analysis = assess(
        client,
        assess_model,
        paper,
        bundle.facts,
        profile,
        pack,
        basis=basis,
        numbers=bundle.numbers,
        figures=figures,
        library=library,
        options=assess_options,
        source_text=_clip(fulltext, MAX_FULLTEXT_PROMPT_CHARS) if fulltext else _clip(paper.summary),
    )
    summary = paper.chinese_summary if numbers_supported(paper.chinese_summary, paper.summary) else ""
    summary = summary or analysis.result
    return _copy_paper(paper, analysis=analysis, chinese_summary=summary)


def analyze_papers(
    client: Any,
    papers: Sequence[Paper],
    profile: Profile,
    packs: Sequence[Pack],
    *,
    extract_model: str,
    assess_model: str,
    limit: int = 0,
    fulltexts: Mapping[str, str] | None = None,
    library: Sequence[ZoteroItem] = (),
    extract_options: LLMOptions | None = None,
    assess_options: LLMOptions | None = None,
    cache_dir: str = "",
) -> list[Paper]:
    """逐篇两阶段分析。单篇失败只影响那一篇（保留旧摘要），不中断整封信。"""
    targets = list(papers[:limit]) if limit and limit > 0 else list(papers)
    target_ids = {paper.id for paper in targets}
    results: dict[str, Paper] = {}
    texts = fulltexts or {}

    for paper in targets:
        try:
            results[paper.id] = analyze_paper(
                client,
                paper,
                profile,
                packs,
                extract_model=extract_model,
                assess_model=assess_model,
                fulltext=texts.get(paper.id),
                library=library,
                extract_options=extract_options,
                assess_options=assess_options,
                cache_dir=cache_dir,
            )
        except Exception as exc:
            LOGGER.warning("两阶段分析失败（%s）：%s，保留原摘要。", paper.id, exc)

    return [results.get(paper.id, paper) if paper.id in target_ids else paper for paper in papers]
