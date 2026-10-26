from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Fact:
    """Stage A 抽出来的一条事实：值 + 原文里能对上的那句话。"""

    value: str | None = None
    quote: str | None = None

    @property
    def is_missing(self) -> bool:
        return not self.value


@dataclass(frozen=True)
class KeyNumber:
    """正文里一个能被引用的数：样本量、红移范围、误差、显著性。"""

    label: str = ""
    value: str = ""
    quote: str | None = None


@dataclass(frozen=True)
class RedFlag:
    """领域包的红旗逐条反查：这篇有没有踩。"""

    flag: str = ""
    status: str = "missing"  # discussed / partial / missing
    note: str = ""

    @property
    def label(self) -> str:
        return {"discussed": "已讨论", "partial": "部分涉及", "missing": "未提及"}.get(
            self.status, "未提及"
        )

    @property
    def tone(self) -> str:
        return {"discussed": "ok", "partial": "warn", "missing": "bad"}.get(self.status, "bad")


@dataclass(frozen=True)
class Concept:
    """论文里出现的、读者画像词表之外的一个术语。"""

    term: str = ""
    gloss: str = ""


@dataclass(frozen=True)
class ScoreBreakdown:
    """排序分的构成 —— 邮件里那几个数字就是从这来的。

    以前卡片只印一个总分，读者没法判断"4.2 分"到底是关键词刷出来的，
    还是真的和方向强相关。把四项拆开显示，分数才可复核、可质疑。
    """

    keyword: float = 0.0
    zotero: float = 0.0
    category: float = 0.0
    library: float = 0.0
    feedback: float = 0.0

    @property
    def total(self) -> float:
        return self.keyword + self.zotero + self.category + self.library + self.feedback

    @property
    def parts(self) -> tuple[tuple[str, float], ...]:
        return (
            ("关键词", self.keyword),
            ("Zotero", self.zotero),
            ("分类", self.category),
            ("库内", self.library),
            ("反馈", self.feedback),
        )

    @property
    def visible_parts(self) -> tuple[tuple[str, float], ...]:
        """只留非零项，避免卡片上一排 0.00 占位。"""

        return tuple(
            (label, value) for label, value in self.parts if abs(value) >= 0.005
        )


@dataclass(frozen=True)
class Analysis:
    """Stage B 的产物：一句话结论 + 结构化七问 + 卡片上其余分区。"""

    relevance: float = 0.0
    relevance_kind: str = "context"
    relevance_reason: str = ""
    data: str = ""
    processing: str = ""
    systematics: str = ""
    method: str = ""
    result: str = ""
    caveat: str = ""
    for_you: str = ""
    confidence: str = "low"
    evidence: tuple[str, ...] = ()
    basis: str = "abstract"
    model: str = ""
    prompt_version: str = ""
    # —— 第 5 批次新增：让精读卡不只是一张表 ——
    verdict: str = ""
    read_advice: str = ""  # read / skim / archive
    read_reason: str = ""
    # —— 第 6 批次新增：先讲"为什么做"，再讲"怎么做的" ——
    background: str = ""
    method_detail: str = ""
    chain: tuple[str, ...] = ()
    numbers: tuple[KeyNumber, ...] = ()
    figures: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    red_flags: tuple[RedFlag, ...] = ()
    concepts: tuple[Concept, ...] = ()
    facts: dict[str, Fact] = field(default_factory=dict)
    source_text: str = ""
    source_sha256: str = ""
    source_scope: str = "abstract"  # abstract / excerpt

    @property
    def fields(self) -> tuple[tuple[str, str, str], ...]:
        """(字段名, 中文标签, 内容) —— 模板按这个顺序渲染。"""
        return (
            ("data", "数据", self.data),
            ("processing", "处理", self.processing),
            ("systematics", "系统误差", self.systematics),
            ("method", "方法", self.method),
            ("result", "结果", self.result),
            ("caveat", "作者局限", self.caveat),
            ("for_you", "对你有用", self.for_you),
        )


@dataclass(frozen=True)
class Paper:
    id: str
    title: str
    authors: list[str]
    summary: str
    published: datetime
    updated: datetime
    categories: list[str]
    pdf_url: str
    abs_url: str
    score: float = 0.0
    rating: int = 1
    score_reasons: list[str] = field(default_factory=list)
    chinese_summary: str = ""
    title_zh: str = ""
    analysis: Analysis | None = None
    # —— 第 6 批次新增 ——
    # 来源期刊缩写（arXiv 留空，APJ/MNRAS/NatAs 等填上），模板据此显示来源徽标
    venue: str = ""
    doi: str = ""
    # 是否已在读者 Zotero 库里：命中就不该再当"新论文"推荐
    in_library: bool = False
    # 期刊论文没有 arXiv 分类，用领域包 id 兜住分派
    pack_hint: str = ""
    breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)


@dataclass(frozen=True)
class ZoteroItem:
    """Zotero 库里的一条记录（不含附件/笔记）。"""

    key: str = ""
    title: str = ""
    item_type: str = ""
    tags: tuple[str, ...] = ()
    abstract: str = ""
    doi: str = ""
    arxiv_id: str = ""
    date_added: str = ""
    collections: tuple[str, ...] = ()
    score: float = 0.0

    @property
    def is_paper(self) -> bool:
        return bool(self.title)


@dataclass(frozen=True)
class ZoteroPreference:
    title: str
    tags: list[str]
    abstract: str
    score: float
