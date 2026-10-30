"""Conservative text/number checks shared by summaries and detailed assessments.

These checks do not prove scientific meaning, units or entailment.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, DecimalException

_NUMBER = re.compile(r"(?<![A-Za-z0-9.])([+-]?\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?)(?:\s*([千万亿]))?")


def _compact(text: str) -> str:
    """压成只含字母/数字/汉字的串，吸收 PDF 抽取造成的断词、连字符与全半角差异。

    PDF 里 "inflation-\ninduced"、"ﬁ" 合字、页码插行、Unicode 引号都会被
    clean_text 弄成和模型抄写不完全一致的样子；直接子串比对会把真引文判成
    编造，整条事实被清空。这里只保留字符序列，词序和字母仍然要对得上。
    """
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\u00ad", "")  # 软连字符
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized.casefold())


def quote_in_source(quote: str | None, source: str) -> bool:
    if not quote:
        return False
    packed = _compact(quote)
    # 太短的串几乎必然在长文里出现，不能算"引文可回查"。
    if len(packed) < 6:
        return False
    if " ".join(quote.split()).casefold() in " ".join(source.split()).casefold():
        return True
    return len(packed) >= 12 and packed in _compact(source)


def _numbers(text: str) -> set[Decimal]:
    text = re.sub(r"(?<=\d)\s*[-–]\s*(?=\d)", " ", text)

    def power(match):
        try:
            exponent = int(match.group(1))
            return str(Decimal(10) ** exponent) if abs(exponent) <= 1024 else match.group(0)
        except (ValueError, DecimalException):
            return match.group(0)

    text = re.sub(r"10\s*\^\s*\{?(-?\d+)\}?", power, text)
    result: set[Decimal] = set()
    for match in _NUMBER.finditer(text):
        try:
            number = Decimal(match.group(1).replace(",", ""))
            number *= {"千": 1000, "万": 10000, "亿": 100000000}.get(match.group(2), 1)
            if number.is_finite():
                result.add(number)
        except DecimalException:
            continue
    return result


def numbers_supported(value: str | None, source: str) -> bool:
    return _numbers(value or "").issubset(_numbers(source))


_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?"

# 日期不是论文结论：中文译文把 "July 2026" 写成 "2026 年 7 月" 会多出数字 7，
# 这类差异不该让整段译文作废。比对前把两边的日期形态一起抹掉。
_DATE_PATTERNS = (
    re.compile(r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?"),
    re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月"),
    re.compile(r"\b\d{4}[-/]\d{1,2}\b"),
    re.compile(rf"\b{_MONTH}\s+\d{{1,2}}(?:,\s*\d{{4}})?\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}\s+{_MONTH}(?:,?\s+\d{{4}})?\b", re.IGNORECASE),
    re.compile(rf"\b{_MONTH}\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日"),
    re.compile(r"\d{1,2}\s*月"),
)


def mask_dates(text: str) -> str:
    """抹掉日期里的数字，只留下可核对的量。"""

    out = text or ""
    for pattern in _DATE_PATTERNS:
        out = pattern.sub(" ", out)
    return out


def numbers_supported_ignoring_dates(value: str | None, source: str) -> bool:
    return numbers_supported(mask_dates(value or ""), mask_dates(source or ""))


_ACTION_QUANTITY = re.compile(
    r"^(?:建议|可先|可以先)(?:抽查|阅读|浏览|核对|挑选)\s*\d+\s*(?:篇|条|张图|个图|分钟)[，,。；;：:\s]*")


def advice_supported(value: str, source: str) -> bool:
    """Permit an explicit reading/checking budget, never a new paper measurement.

    Only a tightly defined initial task quantity is exempt; subsequent factual
    quantities still need source support (including '建议抽查3篇，论文测得999').
    """
    return numbers_supported(_ACTION_QUANTITY.sub("", value), source)
