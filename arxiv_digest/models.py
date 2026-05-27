from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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


@dataclass(frozen=True)
class ZoteroPreference:
    title: str
    tags: list[str]
    abstract: str
    score: float
