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
    chain: tuple[str, ...] = ()
    numbers: tuple[KeyNumber, ...] = ()
    figures: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    red_flags: tuple[RedFlag, ...] = ()
    concepts: tuple[Concept, ...] = ()

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


@dataclass(frozen=True)
class ZoteroPreference:
    title: str
    tags: list[str]
    abstract: str
    score: float
