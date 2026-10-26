"""Conservative text/number checks shared by summaries and detailed assessments.

These checks do not prove scientific meaning, units or entailment.
"""
from __future__ import annotations

import re
from decimal import Decimal, DecimalException

_NUMBER = re.compile(r"(?<![A-Za-z0-9.])([+-]?\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?)(?:\s*([千万亿]))?")


def quote_in_source(quote: str | None, source: str) -> bool:
    return bool(quote and " ".join(quote.split()).casefold() in " ".join(source.split()).casefold())


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


_ACTION_QUANTITY = re.compile(
    r"^(?:建议|可先|可以先)(?:抽查|阅读|浏览|核对|挑选)\s*\d+\s*(?:篇|条|张图|个图|分钟)[，,。；;：:\s]*")


def advice_supported(value: str, source: str) -> bool:
    """Permit an explicit reading/checking budget, never a new paper measurement.

    Only a tightly defined initial task quantity is exempt; subsequent factual
    quantities still need source support (including '建议抽查3篇，论文测得999').
    """
    return numbers_supported(_ACTION_QUANTITY.sub("", value), source)
